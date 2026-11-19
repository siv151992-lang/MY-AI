"""Document intelligence: extraction for every format, OCR, chunk locations, table profiling, generation, citations."""
import pytest

from app.documents import generate, ocr, tables
from app.documents.chunking import chunk_blocks, location
from app.documents.extract import Block, UnsupportedFile, detect, extract
from app.documents.retrieval import validate_citations

from . import doc_fixtures as fx

needs_ocr = pytest.mark.skipif(not ocr.available(), reason="tesseract is not installed")


def test_docx_keeps_headings_and_tables():
    ex = extract("terms.docx", fx.docx_bytes())
    assert ex.doc_type == "docx" and ex.blocks[0].section == "Payment terms"
    assert "Invoices are due within 30 days." in ex.text()
    t = ex.tables[0]
    assert t.columns == ["Item", "Price"] and t.rows == [["Widget", "10"], ["Gadget", "25"]] and t.location == "Payment terms"


def test_xlsx_sheets_formulas_and_row_blocks():
    ex = extract("sales.xlsx", fx.xlsx_bytes())
    t = ex.tables[0]
    assert (t.name, t.columns, t.total_rows, t.formulas) == ("Sales", ["Region", "Q1", "Q2"], 3, 2)
    assert t.rows[0] == ["North", 100, 150]
    assert ex.blocks[0].section == "Sheet Sales rows 1-3" and "Region: North; Q1: 100; Q2: 150" in ex.blocks[0].text
    assert ex.meta["sheets"][0]["formulas"] == 2
    assert any("no saved value" in w for w in ex.warnings)  # openpyxl-written formulas were never calculated


def test_csv_sniffs_delimiter_types_and_headers():
    ex = extract("people.csv", "name;age;city\nAnn;31;Paris\nBo;45;Oslo\n".encode())
    t = ex.tables[0]
    assert t.columns == ["name", "age", "city"] and t.rows[1] == ["Bo", 45, "Oslo"]
    prof = tables.profile(t.columns, t.rows)
    age = next(c for c in prof["columns"] if c["name"] == "age")
    assert age["type"] == "number" and age["mean"] == 38 and age["max"] == 45
    assert next(c for c in prof["columns"] if c["name"] == "city")["type"] == "text"


def test_pptx_slides_tables_and_notes():
    ex = extract("deck.pptx", fx.pptx_bytes())
    assert ex.pages == 2 and [b.page for b in ex.blocks] == [1, 2]
    assert ex.blocks[0].section == "Slide 1: Roadmap" and "- Launch in March" in ex.blocks[0].text
    assert "Speaker notes: Mention hiring" in ex.blocks[1].text and ex.tables[0].rows == [["Eng", "500"]]


def test_markdown_sections_and_plain_text():
    ex = extract("notes.md", b"# Intro\nHello\n\n## Setup\nRun it\n")
    assert [b.section for b in ex.blocks] == ["Intro", "Setup"]
    assert extract("a.txt", "caf\xe9".encode("cp1252")).text() == "café"  # non-UTF-8 text still decodes


def test_detection_rejects_what_it_cannot_read():
    with pytest.raises(UnsupportedFile, match="Legacy Office"):
        detect("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    with pytest.raises(UnsupportedFile, match="ZIP"):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "x")
        detect("a.zip", buf.getvalue()[:16], buf.getvalue())
    with pytest.raises(UnsupportedFile):
        extract("bad.pdf", b"%PDF-1.4 garbage")


@needs_ocr
def test_ocr_reads_images_and_scanned_pdfs():
    img = extract("receipt.png", fx.png_with_text("Invoice total 4,250 EUR"))
    assert img.doc_type == "image" and img.ocr and "4,250" in img.text()
    pdf = extract("scan.pdf", fx.scanned_pdf("Contract signed on 12 March 2025"))
    assert pdf.ocr and pdf.blocks[0].page == 1 and "12 March 2025" in pdf.text()


def test_chunks_keep_page_and_section():
    blocks = [Block("short a", 1, "Intro"), Block("short b", 1, "Intro"), Block("x" * 3000, 2, "Body")]
    chunks = chunk_blocks(blocks, size=1200, overlap=100)
    assert chunks[0].content == "short a\n\nshort b" and chunks[0].page == 1
    assert all(c.page == 2 and c.section == "Body" for c in chunks[1:]) and len(chunks) >= 3
    assert location(3, "Budget", "pptx") == "slide 3, Budget" and location(4, "", "pdf") == "p. 4"


def test_generated_documents_round_trip_through_extraction():
    spec = generate.normalize({"title": "Q3 plan", "sections": [{"heading": "Goals", "paragraphs": ["Grow revenue"],
                               "bullets": ["Hire"], "table": {"columns": ["k", "v"], "rows": [["x", 1]]}}],
                               "sheets": [{"name": "Data/2025", "columns": ["Region", "Revenue"], "rows": [["North", 1200.5], ["South", "900"]]}],
                               "slides": [{"title": "Intro", "bullets": ["one"], "notes": "say hi"}]}, "docx")
    assert spec["sheets"][0]["name"] == "Data2025"
    assert "Grow revenue" in extract("p.docx", generate.render(spec, "docx")).text()
    x = extract("p.xlsx", generate.render(spec, "xlsx")).tables[0]
    assert x.columns == ["Region", "Revenue"] and x.rows[0] == ["North", 1200.5]
    deck = extract("p.pptx", generate.render(spec, "pptx"))
    assert deck.pages == 2 and "Speaker notes: say hi" in deck.text()
    assert generate.render(spec, "pdf").startswith(b"%PDF-")
    assert generate.render(spec, "md").decode().startswith("# Q3 plan")
    with pytest.raises(ValueError):
        generate.normalize({"title": "Empty"}, "pptx")


def test_answers_cannot_cite_passages_that_were_not_retrieved():
    text, cited, removed = validate_citations("Total is 5 [P1]. Due in May [P2, P9]. Pink [P7].", {1, 2})
    assert text == "Total is 5 [P1]. Due in May [P2]. Pink [unsupported]." and cited == [1, 2] and removed == ["P9", "P7"]


def test_table_analysis_prompt_and_code_extraction():
    t = {"name": "Sales", "location": "Sheet Sales", "columns": ["Region", "Q1"], "rows": [["North", 100]], "total_rows": 1,
         "profile": tables.profile(["Region", "Q1"], [["North", 100]])}
    p = tables.build_prompt("Total Q1?", [t])
    assert "t0_sales.csv" in p and "Q1 (number)" in p
    assert tables.extract_code("Here:\n```python\nprint(1)\n```") == "print(1)"
