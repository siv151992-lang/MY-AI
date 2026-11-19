"""Structured extraction: text blocks with locations, plus tables, for every supported format."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field

from . import ocr

MAX_TABLE_ROWS = 5000
MAX_TABLE_COLS = 100
TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs",
            ".c", ".cpp", ".h", ".cs", ".rb", ".php", ".sql", ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
            ".sh", ".log", ".ini", ".properties", ".gradle", ".swift", ".r", ".scala", ".rst", ".tex"}
IMAGE_MIME = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "tiff": "image/tiff",
              "bmp": "image/bmp"}
DOC_MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "csv": "text/csv", "txt": "text/plain"}


class UnsupportedFile(Exception):
    pass


@dataclass
class Block:
    text: str
    page: int | None = None   # PDF page / slide number
    section: str = ""         # heading, "Sheet Sales rows 1-30", "Slide 3: Title"…


@dataclass
class Table:
    name: str
    source: str               # sheet | csv | docx | pptx
    location: str
    columns: list[str]
    rows: list[list]
    total_rows: int
    formulas: int = 0

    def markdown(self, max_rows: int = 30) -> str:
        cell = lambda v: "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")[:80]  # noqa: E731
        out = ["| " + " | ".join(cell(c) for c in self.columns) + " |", "|" + "---|" * len(self.columns)]
        out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in self.rows[:max_rows]]
        if self.total_rows > max_rows:
            out.append(f"… {self.total_rows - max_rows} more rows")
        return "\n".join(out)


@dataclass
class Extraction:
    doc_type: str
    blocks: list[Block] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    pages: int | None = None
    meta: dict = field(default_factory=dict)
    ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = []
        for b in self.blocks:
            loc = f"[Page {b.page}] " if b.page and self.doc_type == "pdf" else f"[Slide {b.page}] " if b.page and self.doc_type == "pptx" else ""
            parts.append(f"{loc}{b.text}")
        return "\n\n".join(parts).replace("\x00", "")


def _ext(filename: str) -> str:
    n = filename.lower()
    return n[n.rfind("."):] if "." in n else ""


def detect(filename: str, head: bytes, data: bytes | None = None) -> str:
    """Decide by content (magic bytes / zip structure), not by the client's MIME type."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:6] in (b"GIF87a", b"GIF89a") \
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[:4] in (b"II*\x00", b"MM\x00*") or head[:2] == b"BM":
        return "image"
    if head.startswith(b"PK\x03\x04") and data is not None:
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            raise UnsupportedFile("This file looks like a damaged archive.")
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        raise UnsupportedFile("ZIP archives are not supported; upload the documents inside it.")
    ext = _ext(filename)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFile(f"Legacy Office format ({ext or 'doc/xls/ppt'}) is not supported; save it as DOCX, XLSX or PPTX.")
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in TEXT_EXT or not ext:
        return "txt"
    raise UnsupportedFile(f"Unsupported file type '{ext}'. Supported: PDF, DOCX, XLSX, CSV, PPTX, text/code files and images.")


def image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return IMAGE_MIME["png"]
    if head.startswith(b"\xff\xd8\xff"):
        return IMAGE_MIME["jpeg"]
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return IMAGE_MIME["gif"]
    if head[:4] == b"RIFF":
        return IMAGE_MIME["webp"]
    if head[:2] == b"BM":
        return IMAGE_MIME["bmp"]
    return IMAGE_MIME["tiff"]


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", errors="replace")


def extract(filename: str, data: bytes) -> Extraction:
    kind = detect(filename, data[:16], data)
    fn = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "csv": _csv, "txt": _txt, "image": _image}[kind]
    ex = fn(data, filename)
    if not ex.blocks and not ex.tables:
        if kind == "image" and not ocr.available():
            ex.warnings.append("OCR is not installed on the server, so no text could be read from this image.")
        elif kind in ("pdf", "image"):
            ex.warnings.append("No text could be found in this file.")
    return ex


# ---------------------------------------------------------------- PDF
def _pdf(data: bytes, filename: str) -> Extraction:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnsupportedFile("This PDF is password-protected.")
        pages = reader.pages
    except PdfReadError:
        raise UnsupportedFile("This PDF is damaged or not a valid PDF.")
    ex = Extraction("pdf", pages=len(pages))
    meta = reader.metadata or {}
    ex.meta = {k: str(meta.get(f"/{k}")) for k in ("Title", "Author", "Subject", "CreationDate") if meta.get(f"/{k}")}
    ocr_done = 0
    for i, page in enumerate(pages, 1):
        try:
            t = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - one bad page shouldn't sink the document
            t = ""
        if len(t) < 30 and ocr.available() and ocr_done < ocr.MAX_OCR_PAGES:
            png = ocr.render_pdf_page(data, i)
            if png:
                o = ocr.ocr_image(png)
                ocr_done += 1
                if len(o) > len(t):
                    t, ex.ocr = o, True
        if t:
            ex.blocks.append(Block(t, page=i))
    if ocr_done >= ocr.MAX_OCR_PAGES and len(pages) > ocr_done:
        ex.warnings.append(f"OCR was limited to the first {ocr.MAX_OCR_PAGES} scanned pages.")
    if ex.ocr:
        ex.meta["ocr_pages"] = ocr_done
    return ex


# ---------------------------------------------------------------- DOCX
def _docx(data: bytes, filename: str) -> Extraction:
    import docx
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph
    d = docx.Document(io.BytesIO(data))
    ex = Extraction("docx")
    section, buf, n_tables = "", [], 0

    def flush():
        if buf:
            ex.blocks.append(Block("\n".join(buf), section=section))
            buf.clear()

    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower().startswith(("heading", "title")):
                flush()
                level = int(m.group(1)) if (m := re.search(r"(\d)", style)) else 1
                section = text[:200]
                buf.append("#" * min(level, 6) + " " + text)
            elif style.lower().startswith("list"):
                buf.append("- " + text)
            else:
                buf.append(text)
        elif tag == "tbl":
            n_tables += 1
            t = DocxTable(child, d)
            rows = [[c.text.strip() for c in r.cells] for r in t.rows]
            if not rows:
                continue
            tbl = _make_table(f"Table {n_tables}", "docx", section or f"Table {n_tables}", rows)
            ex.tables.append(tbl)
            flush()
            ex.blocks.append(Block(f"Table {n_tables}" + (f" (in {section})" if section else "") + ":\n" + tbl.markdown(60), section=section))
    flush()
    props = d.core_properties
    ex.meta = {k: str(v) for k, v in {"Title": props.title, "Author": props.author, "Created": props.created}.items() if v}
    return ex


# ---------------------------------------------------------------- spreadsheets
def _header_row(rows: list[list]) -> int:
    """Index of the header row: the first row whose non-empty cells are mostly text."""
    for i, r in enumerate(rows[:10]):
        vals = [v for v in r if v not in (None, "")]
        if len(vals) >= max(1, len(r) // 2) and sum(isinstance(v, str) for v in vals) >= 0.6 * len(vals):
            return i
    return -1


def _make_table(name: str, source: str, location: str, rows: list[list], formulas: int = 0) -> Table:
    rows = [list(r)[:MAX_TABLE_COLS] for r in rows]
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    # drop trailing empty columns
    while width and all(r[width - 1] in (None, "") for r in rows):
        width -= 1
        rows = [r[:width] for r in rows]
    h = _header_row(rows)
    if h >= 0:
        cols = [str(v).strip() if v not in (None, "") else f"Column {j + 1}" for j, v in enumerate(rows[h])]
        body = rows[h + 1:]
    else:
        cols, body = [f"Column {j + 1}" for j in range(width)], rows
    seen: dict[str, int] = {}
    for j, c in enumerate(cols):  # make column names unique
        if c in seen:
            seen[c] += 1
            cols[j] = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
    body = [r for r in body if any(v not in (None, "") for v in r)]
    return Table(name, source, location, cols, [[_jsonable(v) for v in r] for r in body[:MAX_TABLE_ROWS]], len(body), formulas)


def _jsonable(v):
    import datetime as dt
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def _table_blocks(t: Table, label: str, page: int | None = None, per: int = 25) -> list[Block]:
    """Rows rendered as 'Column: value' lines so retrieval can find individual records."""
    blocks = []
    for start in range(0, len(t.rows), per):
        chunk = t.rows[start:start + per]
        lines = ["; ".join(f"{c}: {v}" for c, v in zip(t.columns, r) if v not in (None, "")) for r in chunk]
        section = f"{label} rows {start + 1}-{start + len(chunk)}"
        blocks.append(Block(f"{section} (columns: {', '.join(t.columns)})\n" + "\n".join(lines), page=page, section=section))
    return blocks


def _xlsx(data: bytes, filename: str) -> Extraction:
    import openpyxl
    ex = Extraction("xlsx")
    values = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        formulas_wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception:  # noqa: BLE001
        formulas_wb = None
    sheets = []
    for ws in values.worksheets:
        rows = [list(r) for _, r in zip(range(MAX_TABLE_ROWS + 10), ws.iter_rows(values_only=True))]
        n_formulas = uncached = 0
        if formulas_wb is not None:
            for i, r in zip(range(MAX_TABLE_ROWS + 10), formulas_wb[ws.title].iter_rows(values_only=True)):
                for j, v in enumerate(r):
                    if isinstance(v, str) and v.startswith("="):
                        n_formulas += 1
                        if i < len(rows) and j < len(rows[i]) and rows[i][j] is None:
                            uncached += 1
        if uncached:
            ex.warnings.append(f"Sheet {ws.title}: {uncached} formula cell(s) have no saved value (the file was not "
                               "recalculated before saving), so they appear empty.")
        if not any(any(v not in (None, "") for v in r) for r in rows):
            continue
        t = _make_table(ws.title, "sheet", f"Sheet {ws.title}", rows, n_formulas)
        if formulas_wb is None and ws.max_row and ws.max_row > len(rows):
            t.total_rows = max(t.total_rows, ws.max_row - 1)
        ex.tables.append(t)
        ex.blocks += _table_blocks(t, f"Sheet {ws.title}")
        sheets.append({"name": ws.title, "rows": t.total_rows, "columns": len(t.columns), "formulas": n_formulas})
        if t.total_rows > len(t.rows):
            ex.warnings.append(f"Sheet {ws.title}: only the first {len(t.rows)} of {t.total_rows} rows are indexed.")
    ex.meta = {"sheets": sheets}
    if any(s["formulas"] for s in sheets):
        ex.meta["note"] = "Formula cells are read as their last calculated values (as saved by Excel)."
    return ex


def _csv(data: bytes, filename: str) -> Extraction:
    text = decode_text(data)
    sample = text[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if filename.lower().endswith(".tsv") else csv.excel
    rows = []
    for i, r in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i > MAX_TABLE_ROWS + 1:
            break
        rows.append([_number(v) for v in r])
    total = sum(1 for _ in csv.reader(io.StringIO(text), dialect))
    ex = Extraction("csv")
    if rows:
        t = _make_table(filename, "csv", filename, rows)
        t.total_rows = max(t.total_rows, total - 1)
        ex.tables.append(t)
        ex.blocks = _table_blocks(t, filename)
        if t.total_rows > len(t.rows):
            ex.warnings.append(f"Only the first {len(t.rows)} of {t.total_rows} rows are indexed.")
    return ex


def _number(v: str):
    s = v.strip()
    if re.fullmatch(r"-?\d{1,15}", s):
        return int(s)
    if re.fullmatch(r"-?\d*\.\d+(e-?\d+)?|-?\d+e-?\d+", s, re.I):
        try:
            return float(s)
        except ValueError:
            return v
    return v


# ---------------------------------------------------------------- PPTX
def _pptx(data: bytes, filename: str) -> Extraction:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    ex = Extraction("pptx", pages=len(prs.slides))
    for i, slide in enumerate(prs.slides, 1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        lines = [f"# Slide {i}: {title}" if title else f"# Slide {i}"]
        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        lines.append(("  " * p.level) + "- " + t)
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
                tbl = _make_table(f"Slide {i} table", "pptx", f"Slide {i}", rows)
                ex.tables.append(tbl)
                lines.append(tbl.markdown(40))
        if slide.has_notes_slide and (notes := slide.notes_slide.notes_text_frame.text.strip()):
            lines.append(f"Speaker notes: {notes}")
        if len(lines) > 1 or title:
            ex.blocks.append(Block("\n".join(lines), page=i, section=f"Slide {i}" + (f": {title}" if title else "")))
    return ex


# ---------------------------------------------------------------- text & images
def _txt(data: bytes, filename: str) -> Extraction:
    text = decode_text(data).replace("\r\n", "\n")
    ex = Extraction("txt")
    if _ext(filename) in (".md", ".markdown", ".rst"):
        section, buf = "", []
        for line in text.split("\n"):
            if re.match(r"#{1,6}\s", line):
                if "".join(buf).strip():
                    ex.blocks.append(Block("\n".join(buf).strip(), section=section))
                buf, section = [line], line.lstrip("# ").strip()[:200]
            else:
                buf.append(line)
        if "".join(buf).strip():
            ex.blocks.append(Block("\n".join(buf).strip(), section=section))
    elif text.strip():
        ex.blocks.append(Block(text.strip()))
    return ex


def _image(data: bytes, filename: str) -> Extraction:
    ex = Extraction("image", pages=1)
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        ex.meta = {"width": img.width, "height": img.height, "format": img.format}
    except Exception:  # noqa: BLE001
        raise UnsupportedFile("This image could not be opened.")
    text = ocr.ocr_image(data)
    if text:
        ex.ocr = True
        ex.blocks.append(Block(text, section="Text found in the image (OCR)"))
    return ex
