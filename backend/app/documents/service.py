"""Document intelligence operations. Every function takes the acting user and checks access first."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from .. import db
from ..agent.jsonutil import extract_json
from ..config import get_settings
from ..llm.formats import text_msg
from ..research.evidence import quote_in_source
from . import access, generate, retrieval, store, tables
from .chunking import chunk_blocks
from .extract import DOC_MIME, Extraction, UnsupportedFile, extract, image_mime

log = logging.getLogger("aria.documents")
_background: set[asyncio.Task] = set()

SUMMARY_SYSTEM = """Summarise the document for the user. {style} Be faithful: only state what the document says, keep \
key numbers, names, dates, obligations and conclusions, and mention the page/slide/sheet for important points when \
the text shows it (e.g. "(p. 4)"). If the text is partial or garbled (e.g. poor OCR), say so."""
STYLES = {"brief": "Write 3-5 sentences.", "bullets": "Write 5-10 concise bullet points.",
          "detailed": "Write a structured summary with short headed sections and bullet points (up to ~500 words)."}
MERGE_SYSTEM = "Merge these partial summaries of consecutive parts of one document into a single summary. {style}"

ASK_SYSTEM = """Answer the user's question using ONLY the numbered document passages. Cite every factual statement \
with the passage markers, like [P2] or [P1, P4]. If the passages don't contain the answer, say clearly that the \
documents don't cover it (and what they do cover) instead of guessing. Quote exact figures. Be concise."""

COMPARE_SYSTEM = """Compare the documents using ONLY the numbered passages (each is labelled with its document). \
Cite every statement with passage markers like [P3]. Structure:
## Overview (one line per document)
## Comparison (a Markdown table: one row per aspect, one column per document; cite in cells; write "not stated" \
where a document doesn't cover an aspect)
## Key differences
## Similarities
## Gaps and caveats (what couldn't be compared from the available text)"""

EXTRACT_SYSTEM = """Extract the requested fields from the document. For each field return the value and the exact \
text from the document it came from (copied character for character). If a field is not in the document, return \
null for both — never guess or infer values that are not stated.
Reply with JSON only: {"fields": {"<field name>": {"value": <string|number|list|null>, "quote": "<exact text or null>"}}}"""

PRESETS = {
    "invoice": [("invoice_number", "Invoice number"), ("invoice_date", "Date of the invoice"), ("due_date", "Payment due date"),
                ("vendor", "Seller / supplier name"), ("customer", "Buyer name"), ("currency", "Currency"),
                ("subtotal", "Amount before tax"), ("tax", "Tax amount"), ("total", "Total amount due"),
                ("line_items", "List of items with quantity and price")],
    "contract": [("parties", "The contracting parties"), ("effective_date", "When the contract starts"),
                 ("term", "Duration / end date"), ("payment_terms", "Payment amounts and schedule"),
                 ("termination", "How either party can terminate"), ("governing_law", "Governing law / jurisdiction"),
                 ("confidentiality", "Confidentiality obligations"), ("liability_cap", "Limitation of liability")],
    "resume": [("name", "Candidate name"), ("email", "Email address"), ("current_role", "Most recent job title and employer"),
               ("years_experience", "Total years of experience"), ("skills", "Key skills"), ("education", "Degrees and institutions"),
               ("languages", "Spoken languages")],
    "research_paper": [("title", "Paper title"), ("authors", "Authors"), ("year", "Publication year"),
                       ("research_question", "Question or hypothesis"), ("method", "Methodology and sample"),
                       ("findings", "Main findings with numbers"), ("limitations", "Stated limitations")],
}


def _models():
    from ..llm import registry
    spec, uspec = registry.get_model(None), registry.utility_model()
    return (registry.provider_for(spec), spec.model), (registry.provider_for(uspec), uspec.model)


async def _complete(system: str, prompt: str, *, main: bool = False, max_tokens: int = 1500) -> str:
    (ml, mm), (ul, um) = _models()
    llm, model = (ml, mm) if main else (ul, um)
    return await llm.complete(model=model, system=system, max_tokens=max_tokens, messages=[text_msg("user", prompt)])


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


# ---------------------------------------------------------------- ingestion
async def ingest(user_id: str, filename: str, data: bytes, *, meta: dict | None = None, summarize: bool = True) -> dict:
    """Store, extract, index. Raises UnsupportedFile for files we can't read (nothing is stored then)."""
    s = get_settings()
    filename = Path(filename or "upload").name[:200]
    try:
        ex: Extraction = await asyncio.to_thread(extract, filename, data)
    except UnsupportedFile:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("extraction failed for %s: %s", filename, e)
        raise UnsupportedFile("Could not read this file. Is it corrupted or password-protected?")
    kind = "image" if ex.doc_type == "image" else "document"
    mime = image_mime(data[:16]) if kind == "image" else DOC_MIME.get(ex.doc_type, "text/plain")
    file_id = str(uuid.uuid4())
    user_dir = s.upload_dir / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / file_id  # never the client's filename on disk
    await asyncio.to_thread(path.write_bytes, data)
    text = ex.text()
    rec = await db.create_file(user_id, id=file_id, filename=filename, mime_type=mime, size_bytes=len(data),
                               storage_path=str(path), kind=kind, text_content=text or None, status="processing")
    try:
        await _index(user_id, file_id, ex, extra_meta=meta)
    except Exception as e:  # noqa: BLE001
        log.exception("indexing failed")
        await store.update_doc(file_id, status="failed", error=f"Indexing failed: {type(e).__name__}")
        raise
    if summarize and text.strip():
        _spawn(_auto_summary(user_id, file_id))
    if kind == "image":
        _spawn(_describe_image(user_id, file_id, data, mime))
    return {**rec, "status": "ready", "doc_type": ex.doc_type, "chunks": rec.get("chunks"), "warnings": ex.warnings,
            "ocr": ex.ocr, "chars": len(text)}


async def _index(owner_id: str, file_id: str, ex: Extraction, extra_meta: dict | None = None) -> None:
    from ..llm import registry
    chunks = chunk_blocks(ex.blocks)
    embs = None
    if chunks:
        try:
            embs = await registry.embed([c.content for c in chunks])
        except Exception:  # noqa: BLE001 - keyword search still works; embeddings are backfilled later
            embs = None
    await store.replace_chunks(owner_id, file_id, chunks, embs)
    await store.replace_tables(file_id, ex.tables, [tables.profile(t.columns, t.rows, t.total_rows) for t in ex.tables])
    await store.update_doc(file_id, status="ready", doc_type=ex.doc_type, pages=ex.pages, ocr=ex.ocr,
                           meta={**ex.meta, **(extra_meta or {})}, warnings=ex.warnings, error="", text_content=ex.text() or None)


async def reindex(user_id: str, file_id: str) -> dict:
    await access.require(user_id, file_id, access.MANAGE)
    doc = await store.get_doc(user_id, file_id)
    data = await asyncio.to_thread(Path(doc["storage_path"]).read_bytes)
    ex = await asyncio.to_thread(extract, doc["filename"], data)
    await _index(user_id, file_id, ex, extra_meta={k: v for k, v in (doc["meta"] or {}).items() if k in ("generated", "sources", "prompt")})
    return await store.get_doc(user_id, file_id)


async def _auto_summary(user_id: str, file_id: str) -> None:
    try:
        await summarize(user_id, file_id, "brief", force=True)
    except Exception:  # noqa: BLE001
        log.exception("auto summary failed")


async def _describe_image(user_id: str, file_id: str, data: bytes, mime: str) -> None:
    """Image understanding: with a vision model configured, add a description to the searchable text."""
    import base64

    from ..llm import registry
    try:
        spec = next((m for m in registry.available_models() if m.vision), None)
        if not spec:
            return
        msg = {"role": "user", "content": [
            {"type": "image", "media_type": mime, "data": base64.b64encode(data).decode()},
            {"type": "text", "text": "Describe this image for a document index: what it shows, any text, numbers, charts "
                                     "(axes, trends, values), tables or diagrams. Be factual and specific."}]}
        desc = await registry.provider_for(spec).complete(model=spec.model, system="You describe images accurately.",
                                                          max_tokens=800, messages=[msg])
        doc = await store.get_doc(user_id, file_id)
        ocr_text = doc["text_content"] or ""
        from .extract import Block
        blocks = ([Block(ocr_text, section="Text found in the image (OCR)")] if ocr_text else []) + \
                 [Block(desc.strip(), section="Image description (AI)")]
        ex = Extraction("image", blocks=blocks, pages=1, meta={**(doc["meta"] or {}), "described_by": spec.id}, ocr=doc["ocr"])
        await _index(user_id, file_id, ex)
    except Exception:  # noqa: BLE001
        log.exception("image description failed")


# ---------------------------------------------------------------- reading
async def text_of(user_id: str, file_id: str) -> tuple[dict, str]:
    await access.require(user_id, file_id)
    doc = await store.get_doc(user_id, file_id)
    return doc, doc.get("text_content") or ""


async def summarize(user_id: str, file_id: str, style: str = "brief", force: bool = False) -> str:
    doc, text = await text_of(user_id, file_id)
    if style == "brief" and doc.get("summary") and not force:
        return doc["summary"]
    if not text.strip():
        return "This document has no readable text to summarise."
    st = STYLES.get(style, STYLES["brief"])
    extra = ""
    if doc["tables"]:
        tbls = await store.tables(file_id)
        extra = "\n\nTables in the document:\n" + "\n".join(
            f"- {t['name']} ({t['location']}): {t['total_rows']} rows; columns: " +
            ", ".join(f"{c['name']} ({c['type']}" + (f", {c['min']}–{c['max']}" if c.get("type") == "number" else "") + ")"
                      for c in t["profile"].get("columns", [])[:20]) for t in tbls[:10])
    parts = [text[i:i + 24_000] for i in range(0, len(text), 24_000)][:12]
    if len(parts) == 1:
        out = await _complete(SUMMARY_SYSTEM.format(style=st), f"Document: {doc['filename']}\n\n{parts[0]}{extra}", max_tokens=1200)
    else:
        partials = await asyncio.gather(*(_complete(SUMMARY_SYSTEM.format(style=STYLES["bullets"]),
                                                    f"Part {i + 1} of {len(parts)} of {doc['filename']}:\n\n{p}", max_tokens=800)
                                          for i, p in enumerate(parts)))
        out = await _complete(MERGE_SYSTEM.format(style=st), "\n\n---\n\n".join(partials) + extra, max_tokens=1500)
        if len(text) > 24_000 * 12:
            out += "\n\n_(Summary based on the first ~290,000 characters of a longer document.)_"
    if style == "brief":
        await store.update_doc(file_id, summary=out.strip())
    return out.strip()


async def ask(user_id: str, question: str, file_ids: list[str] | None = None, k: int = 8) -> dict:
    passages = await retrieval.retrieve(user_id, question, file_ids, k=k)
    if not passages:
        return {"answer": "I couldn't find any content to answer from: none of the selected documents (or your library) "
                          "returned relevant passages.", "citations": [], "passages": 0, "removed_citations": []}
    reply = await _complete(ASK_SYSTEM, f"Question: {question}\n\nPassages:\n{retrieval.context(passages)}", main=True, max_tokens=1500)
    answer, cited, removed = retrieval.validate_citations(reply, {p.n for p in passages})
    by_n = {p.n: p for p in passages}
    return {"answer": answer, "citations": [by_n[n].cite() for n in cited], "passages": len(passages), "removed_citations": removed}


async def compare(user_id: str, file_ids: list[str], focus: str = "") -> dict:
    ids = await access.filter_readable(user_id, file_ids)
    if len(ids) < 2:
        raise ValueError("Select at least two documents you have access to.")
    docs = await store.get_docs(user_id, ids)
    query = focus or "main purpose, key terms, figures, dates, obligations, conclusions"
    passages = await retrieval.retrieve(user_id, query, ids, k=6 * len(ids), per_doc=5)
    names = "\n".join(f"- {d['filename']}" + (f" — summary: {d['summary'][:400]}" if d.get("summary") else "") for d in docs)
    prompt = (f"Documents:\n{names}\n\n" + (f"Focus of the comparison: {focus}\n\n" if focus else "")
              + f"Passages:\n{retrieval.context(passages, 80_000)}")
    reply = await _complete(COMPARE_SYSTEM, prompt, main=True, max_tokens=3000)
    text, cited, removed = retrieval.validate_citations(reply, {p.n for p in passages})
    by_n = {p.n: p for p in passages}
    return {"comparison": text, "documents": [{"id": d["id"], "filename": d["filename"]} for d in docs],
            "citations": [by_n[n].cite() for n in cited], "removed_citations": removed}


def _field_list(fields: list[dict] | None, preset: str | None) -> list[tuple[str, str]]:
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"Unknown preset. Choose one of: {', '.join(PRESETS)}")
        return PRESETS[preset]
    out = []
    for f in fields or []:
        name = str(f.get("name", "")).strip()[:80]
        if name:
            out.append((name, str(f.get("description", "")).strip()[:300]))
    if not out:
        raise ValueError("Give at least one field to extract, or a preset.")
    return out[:40]


async def extract_fields(user_id: str, file_id: str, fields: list[dict] | None = None, preset: str | None = None) -> dict:
    """Structured data extraction. Every value must come with a quote that is found in the document; values
    whose quote isn't in the text are returned as unverified, so nothing is silently invented."""
    doc, text = await text_of(user_id, file_id)
    wanted = _field_list(fields, preset)
    if not text.strip():
        raise ValueError("This document has no readable text.")
    if len(text) > 60_000:  # long document: give the model the passages most relevant to the fields
        passages = await retrieval.retrieve(user_id, " ".join(f"{n} {d}" for n, d in wanted), [file_id], k=16)
        body = retrieval.context(passages, 60_000)
    else:
        body = text
    spec = "\n".join(f"- {n}: {d}" for n, d in wanted)
    reply = await _complete(EXTRACT_SYSTEM, f"Fields:\n{spec}\n\nDocument ({doc['filename']}):\n{body}", main=True, max_tokens=2500)
    data = extract_json(reply)
    got = data.get("fields", data) if isinstance(data, dict) else {}
    results = []
    for name, desc in wanted:
        item = got.get(name) if isinstance(got, dict) else None
        value = item.get("value") if isinstance(item, dict) else item
        quote = item.get("quote") if isinstance(item, dict) else None
        if value in (None, "", []):
            status = "not_found"
            value, quote = None, None
        elif quote and quote_in_source(str(quote), text) or (quote and str(quote).strip() and str(quote).strip().lower() in text.lower()):
            status = "verified"
        else:
            status = "unverified"  # the model gave a value but we couldn't find its supporting text
        results.append({"field": name, "description": desc, "value": value, "quote": quote, "status": status})
    return {"document": {"id": doc["id"], "filename": doc["filename"]}, "fields": results}


async def table_list(user_id: str, file_id: str) -> list[dict]:
    await access.require(user_id, file_id)
    return await store.tables(file_id)


async def table_rows(user_id: str, file_id: str, table_id: str, offset: int = 0, limit: int = 100) -> dict:
    await access.require(user_id, file_id)
    t = await store.table(file_id, table_id)
    if not t:
        raise access.NotFound(table_id)
    return {**{k: v for k, v in t.items() if k != "rows"}, "offset": offset, "rows": t["rows"][offset:offset + limit]}


async def analyze_table(user_id: str, file_id: str, question: str, table_ids: list[str] | None = None) -> dict:
    """Spreadsheet / table analysis: the model writes pandas code, which runs in the sandbox over the full data."""
    await access.require(user_id, file_id)
    all_tables = await store.tables(file_id, with_rows=True)
    chosen = [t for t in all_tables if not table_ids or t["id"] in table_ids] or all_tables
    if not chosen:
        raise ValueError("This document has no tables.")
    s = get_settings()
    files = {tables.safe_name(t["name"], i): tables.to_csv(t["columns"], t["rows"]).encode() for i, t in enumerate(chosen)}
    prompt = tables.build_prompt(question, chosen)
    code, result = "", {}
    for attempt in range(2):
        reply = await _complete(tables.ANALYZE_SYSTEM, prompt, main=True, max_tokens=2000)
        code = tables.extract_code(reply)
        result = await tables.run_in_sandbox({**files, "analysis.py": code.encode()}, "python3 analysis.py",
                                             sandbox_url=s.sandbox_url, token=s.sandbox_token)
        if result.get("exit_code") == 0 and (result.get("stdout") or "").strip():
            break
        prompt += f"\n\nYour previous script failed:\n```python\n{code}\n```\nstderr:\n{(result.get('stderr') or '')[-3000:]}\nFix it."
    ok = result.get("exit_code") == 0 and bool((result.get("stdout") or "").strip())
    explanation = await _complete(tables.EXPLAIN_SYSTEM, f"Question: {question}\n\nOutput:\n{(result.get('stdout') or '')[:8000]}"
                                  + ("" if ok else f"\n\nError:\n{(result.get('stderr') or '')[-2000:]}"), max_tokens=600)
    truncated = [t["name"] for t in chosen if len(t["rows"]) < t["total_rows"]]
    return {"ok": ok, "answer": explanation.strip(), "code": code, "output": (result.get("stdout") or "")[:20_000],
            "error": "" if ok else (result.get("stderr") or "")[-4000:],
            "tables": [t["name"] for t in chosen],
            "note": f"Analysed the first {min(len(t['rows']) for t in chosen)} rows of {', '.join(truncated)}" if truncated else ""}


async def generate_document(user_id: str, prompt: str, fmt: str, source_ids: list[str] | None = None,
                            filename: str | None = None) -> dict:
    if fmt not in generate.FORMATS:
        raise ValueError(f"Format must be one of {', '.join(generate.FORMATS)}")
    sources = await access.filter_readable(user_id, source_ids or [])
    passages = await retrieval.retrieve(user_id, prompt, sources, k=24, per_doc=4) if sources else []
    ctx = f"\n\nSource excerpts:\n{retrieval.context(passages, 80_000)}" if passages else ""
    reply = await _complete(generate.SPEC_SYSTEM.format(fmt=fmt.upper(), guidance=generate.GUIDANCE[fmt]),
                            f"Request: {prompt}{ctx}", main=True, max_tokens=8000)
    spec = generate.normalize(reply, fmt)
    data = await asyncio.to_thread(generate.render, spec, fmt)
    base = (filename or spec["title"]).strip()
    safe = "".join(c if c.isalnum() or c in " -_." else "-" for c in base)[:80].strip(" .") or "document"
    name = safe if safe.lower().endswith(generate.FORMATS[fmt][1]) else safe + generate.FORMATS[fmt][1]
    cited = sorted({int(n) for n in __import__("re").findall(r"\[P(\d+)", json.dumps(spec))} & {p.n for p in passages})
    rec = await ingest(user_id, name, data, meta={"generated": True, "prompt": prompt[:1000],
                                                  "sources": [{"id": p.file_id, "filename": p.filename, "location": p.where}
                                                              for p in passages if p.n in cited][:50]}, summarize=False)
    return rec


async def delete(user_id: str, file_id: str) -> None:
    await access.require(user_id, file_id, access.MANAGE)
    rec = await db.delete_file(user_id, file_id)
    if rec:
        Path(rec["storage_path"]).unlink(missing_ok=True)


async def backfill_embeddings(limit: int = 200) -> int:
    """Embed chunks indexed while the embedding provider was unavailable."""
    from ..llm import registry
    missing = await store.missing_embeddings(limit)
    if not missing:
        return 0
    try:
        embs = await registry.embed([m["content"] for m in missing])
    except Exception:  # noqa: BLE001
        return 0
    if not embs:
        return 0
    for m, e in zip(missing, embs):
        await store.set_chunk_embedding(m["id"], e)
    return len(missing)
