"""Document generation: a structured spec (written by the model) rendered to DOCX, XLSX, PPTX, PDF or Markdown."""
from __future__ import annotations

import io
import re

from ..agent.jsonutil import extract_json

FORMATS = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "pdf": ("application/pdf", ".pdf"),
    "md": ("text/markdown", ".md"),
}

SPEC_SYSTEM = """You produce documents as structured JSON that is then rendered to {fmt}. Write complete, polished \
content (no placeholders like "[insert here]"). When source excerpts are provided, base the content on them, cite \
them inline as [P#] where you use them, and do not invent facts, figures or quotes that are not in them; if the \
request needs information the sources don't contain, say so in the document instead of making it up.

JSON shape (include only the parts relevant to the format):
{{"title": "...",
 "sections": [{{"heading": "...", "level": 1, "paragraphs": ["..."], "bullets": ["..."],
               "table": {{"columns": ["..."], "rows": [["...", 1.5]]}}}}],
 "sheets": [{{"name": "...", "columns": ["..."], "rows": [["text", 12.5, "2025-01-31"]]}}],
 "slides": [{{"title": "...", "bullets": ["..."], "notes": "...", "table": {{"columns": [], "rows": []}}}}]}}
Format guidance: {guidance}
Reply with JSON only."""

GUIDANCE = {
    "docx": "use sections (headings, paragraphs, bullets, tables).",
    "pdf": "use sections (headings, paragraphs, bullets, tables).",
    "md": "use sections (headings, paragraphs, bullets, tables).",
    "xlsx": "use sheets; numbers must be JSON numbers (not strings) so they can be calculated; first sheet is the main one. You may add a short 'Notes' sheet.",
    "pptx": "use slides: 5-12 slides, a short title each, 3-6 concise bullets, optional speaker notes and small tables.",
}


def _s(v, n: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _table(t) -> dict | None:
    if not isinstance(t, dict):
        return None
    cols = [_s(c, 100) for c in (t.get("columns") or [])][:30]
    rows = [[c if isinstance(c, (int, float)) and not isinstance(c, bool) else _s(c, 500) for c in r][:30]
            for r in (t.get("rows") or []) if isinstance(r, list)][:2000]
    return {"columns": cols, "rows": rows} if cols and rows else None


def normalize(raw, fmt: str) -> dict:
    data = extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("The model did not return a document structure.")
    spec = {"title": _s(data.get("title"), 200) or "Document", "sections": [], "sheets": [], "slides": []}
    for sec in data.get("sections") or []:
        if isinstance(sec, dict):
            spec["sections"].append({"heading": _s(sec.get("heading"), 200), "level": min(max(int(sec.get("level") or 1), 1), 3),
                                     "paragraphs": [_s(p, 6000) for p in sec.get("paragraphs") or [] if _s(p)],
                                     "bullets": [_s(b, 1000) for b in sec.get("bullets") or [] if _s(b)],
                                     "table": _table(sec.get("table"))})
    for sh in data.get("sheets") or []:
        if t := _table(sh):
            spec["sheets"].append({"name": re.sub(r"[\[\]:*?/\\]", "", _s(sh.get("name"), 31)) or f"Sheet{len(spec['sheets']) + 1}", **t})
    for sl in data.get("slides") or []:
        if isinstance(sl, dict):
            spec["slides"].append({"title": _s(sl.get("title"), 150), "bullets": [_s(b, 400) for b in sl.get("bullets") or [] if _s(b)][:10],
                                   "notes": _s(sl.get("notes"), 3000), "table": _table(sl.get("table"))})
    needed = {"xlsx": "sheets", "pptx": "slides"}.get(fmt, "sections")
    if not spec[needed]:
        if fmt == "xlsx" and spec["sections"]:  # tolerate a model that used tables in sections
            spec["sheets"] = [{"name": (s["heading"] or f"Sheet{i + 1}")[:31], **s["table"]} for i, s in enumerate(spec["sections"]) if s["table"]]
        if not spec[needed]:
            raise ValueError(f"The generated document has no {needed}.")
    return spec


def to_markdown(spec: dict) -> str:
    out = [f"# {spec['title']}"]
    for s in spec["sections"]:
        if s["heading"]:
            out.append("#" * (s["level"] + 1) + " " + s["heading"])
        out += s["paragraphs"]
        if s["bullets"]:
            out.append("\n".join(f"- {b}" for b in s["bullets"]))
        if s["table"]:
            out.append(_md_table(s["table"]))
    for sl in spec["slides"]:
        out.append(f"## {sl['title']}")
        if sl["bullets"]:
            out.append("\n".join(f"- {b}" for b in sl["bullets"]))
        if sl["table"]:
            out.append(_md_table(sl["table"]))
    for sh in spec["sheets"]:
        out += [f"## {sh['name']}", _md_table(sh)]
    return "\n\n".join(out) + "\n"


def _md_table(t: dict) -> str:
    cell = lambda v: str(v).replace("|", "\\|")  # noqa: E731
    return "\n".join(["| " + " | ".join(cell(c) for c in t["columns"]) + " |", "|" + "---|" * len(t["columns"])]
                     + ["| " + " | ".join(cell(v) for v in r) + " |" for r in t["rows"]])


def to_docx(spec: dict) -> bytes:
    import docx
    d = docx.Document()
    d.add_heading(spec["title"], 0)
    for s in spec["sections"]:
        if s["heading"]:
            d.add_heading(s["heading"], s["level"])
        for p in s["paragraphs"]:
            d.add_paragraph(p)
        for b in s["bullets"]:
            d.add_paragraph(b, style="List Bullet")
        if t := s["table"]:
            tbl = d.add_table(rows=1, cols=len(t["columns"]))
            tbl.style = "Light Grid Accent 1"
            for j, c in enumerate(t["columns"]):
                tbl.rows[0].cells[j].text = c
            for r in t["rows"]:
                cells = tbl.add_row().cells
                for j, v in enumerate(r[:len(t["columns"])]):
                    cells[j].text = str(v)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def to_xlsx(spec: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sh in spec["sheets"]:
        ws = wb.create_sheet(sh["name"][:31])
        ws.append(sh["columns"])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E8EEF7")
        for r in sh["rows"]:
            ws.append(r)
        ws.freeze_panes = "A2"
        for j, col in enumerate(sh["columns"], 1):
            width = max([len(str(col))] + [len(str(r[j - 1])) for r in sh["rows"][:200] if len(r) >= j])
            ws.column_dimensions[get_column_letter(j)].width = min(60, max(10, width + 2))
    wb.properties.title = spec["title"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pptx(spec: dict) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    first = prs.slides.add_slide(prs.slide_layouts[0])
    first.shapes.title.text = spec["title"]
    for sl in spec["slides"]:
        if sl["table"]:
            s = prs.slides.add_slide(prs.slide_layouts[5])
            s.shapes.title.text = sl["title"]
            t = sl["table"]
            rows, cols = min(len(t["rows"]), 12) + 1, len(t["columns"])
            tbl = s.shapes.add_table(rows, cols, Inches(0.5), Inches(1.6), Inches(9), Inches(0.4) * rows).table
            for j, c in enumerate(t["columns"]):
                tbl.cell(0, j).text = c
            for i, r in enumerate(t["rows"][:rows - 1], 1):
                for j, v in enumerate(r[:cols]):
                    tbl.cell(i, j).text = str(v)
            if sl["bullets"]:
                box = s.shapes.add_textbox(Inches(0.5), Inches(1.6) + Inches(0.4) * rows + Inches(0.2), Inches(9), Inches(1)).text_frame
                box.text = " · ".join(sl["bullets"])
        else:
            s = prs.slides.add_slide(prs.slide_layouts[1])
            s.shapes.title.text = sl["title"]
            body = s.placeholders[1].text_frame
            for i, b in enumerate(sl["bullets"]):
                p = body.paragraphs[0] if i == 0 else body.add_paragraph()
                p.text = b
                p.font.size = Pt(20)
        if sl["notes"]:
            s.notes_slide.notes_text_frame.text = sl["notes"]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def render(spec: dict, fmt: str) -> bytes:
    if fmt == "md":
        return to_markdown(spec).encode()
    if fmt == "pdf":
        from ..research.report import to_pdf
        return to_pdf(to_markdown(spec), spec["title"])
    return {"docx": to_docx, "xlsx": to_xlsx, "pptx": to_pptx}[fmt](spec)
