"""Chat agent tools over the user's document library (own documents plus ones shared with them)."""
from __future__ import annotations

import json

from ..agent.tools.base import Tool, ToolContext
from . import access, retrieval, service, store


async def _search(args: dict, ctx: ToolContext) -> str:
    ids = ctx.file_ids if args.get("only_attached") and ctx.file_ids else (args.get("document_ids") or None)
    passages = await retrieval.retrieve(ctx.user_id, str(args["query"]), ids, k=int(args.get("k") or 8))
    if not passages:
        return "No matching passages found in the user's documents."
    return ("Cite passages you use as (filename, location).\n\n"
            + "\n\n".join(f"[{p.filename}" + (f" · {p.where}" if p.where else "") + f" · id {p.file_id}]\n{p.content}" for p in passages))


async def _list(args: dict, ctx: ToolContext) -> str:
    docs = await store.list_docs(ctx.user_id)
    if not docs:
        return "The user has no documents."
    return "\n".join(f"- {d['filename']} (id {d['id']}; {d['doc_type'] or d['kind']}"
                     + (f", {d['pages']} pages" if d["pages"] else "") + (f", {d['tables']} tables" if d["tables"] else "")
                     + ("" if d["owned"] else f", shared by {d['owner_name']}") + ")"
                     + (f": {d['summary'][:200]}" if d.get("summary") else "") for d in docs[:100])


async def _read(args: dict, ctx: ToolContext) -> str:
    fid = str(args["document_id"])
    await access.require(ctx.user_id, fid)
    chunks = await store.chunks(fid)
    page = args.get("page")
    if page:
        chunks = [c for c in chunks if c["page"] == int(page)]
    start = int(args.get("start_chunk") or 0)
    sel = chunks[start:start + 12]
    if not sel:
        return "Nothing at that location."
    more = f"\n\n… {len(chunks) - start - len(sel)} more chunks (use start_chunk={start + len(sel)})" if len(chunks) > start + len(sel) else ""
    return "\n\n".join((f"[p. {c['page']}] " if c["page"] else "") + (f"[{c['section']}] " if c["section"] else "") + c["content"] for c in sel) + more


async def _summarize(args: dict, ctx: ToolContext) -> str:
    return await service.summarize(ctx.user_id, str(args["document_id"]), args.get("style", "bullets"))


async def _compare(args: dict, ctx: ToolContext) -> str:
    r = await service.compare(ctx.user_id, list(args["document_ids"]), str(args.get("focus", "")))
    refs = "\n".join(f"[P{c['n']}] = {c['filename']} ({c['location']})" for c in r["citations"])
    return r["comparison"] + ("\n\nPassage key:\n" + refs if refs else "")


async def _extract(args: dict, ctx: ToolContext) -> str:
    r = await service.extract_fields(ctx.user_id, str(args["document_id"]), args.get("fields"), args.get("preset"))
    return json.dumps(r["fields"], ensure_ascii=False, indent=1)[:12000]


async def _analyze(args: dict, ctx: ToolContext) -> str:
    r = await service.analyze_table(ctx.user_id, str(args["document_id"]), str(args["question"]))
    return (f"{'Result' if r['ok'] else 'Analysis failed'} (tables: {', '.join(r['tables'])})\n{r['answer']}\n\nOutput:\n{r['output'][:6000]}"
            + (f"\nError:\n{r['error']}" if r["error"] else "") + (f"\nNote: {r['note']}" if r["note"] else ""))


async def _generate(args: dict, ctx: ToolContext) -> str:
    r = await service.generate_document(ctx.user_id, str(args["prompt"]), str(args.get("format", "docx")),
                                        args.get("source_ids") or [], args.get("filename"))
    return f"Created {r['filename']} in the user's documents. Download: /api/documents/{r['id']}/download"


def make_document_tools() -> list[Tool]:
    obj = lambda p, r: {"type": "object", "properties": p, "required": r}  # noqa: E731
    doc_id = {"type": "string", "description": "Document id from list_documents or search results"}
    return [
        Tool("search_documents", "Search the user's documents (uploads, generated files and documents shared with them) "
                                 "for passages relevant to a query. Results show filename, page/section and document id.",
             obj({"query": {"type": "string"}, "only_attached": {"type": "boolean", "description": "Only files attached in this chat"},
                  "document_ids": {"type": "array", "items": {"type": "string"}}, "k": {"type": "integer"}}, ["query"]),
             _search, timeout=30),
        Tool("list_documents", "List the user's documents with ids, types, sizes, tables and short summaries.",
             obj({}, []), _list, timeout=20),
        Tool("read_document", "Read a document's text in order (optionally one page/slide), 12 chunks at a time.",
             obj({"document_id": doc_id, "page": {"type": "integer"}, "start_chunk": {"type": "integer"}}, ["document_id"]),
             _read, timeout=20),
        Tool("summarize_document", "Summarise a whole document (map-reduce over long files).",
             obj({"document_id": doc_id, "style": {"type": "string", "enum": ["brief", "bullets", "detailed"]}}, ["document_id"]),
             _summarize, timeout=180),
        Tool("compare_documents", "Compare 2-6 documents (optionally on a focus such as 'pricing and termination'); "
                                  "returns a cited comparison with a table.",
             obj({"document_ids": {"type": "array", "items": {"type": "string"}}, "focus": {"type": "string"}}, ["document_ids"]),
             _compare, timeout=180),
        Tool("extract_data", "Extract structured fields from a document, each with its verified source text. Use a preset "
                             "(invoice, contract, resume, research_paper) or give fields [{name, description}].",
             obj({"document_id": doc_id, "preset": {"type": "string"},
                  "fields": {"type": "array", "items": obj({"name": {"type": "string"}, "description": {"type": "string"}}, ["name"])}},
                 ["document_id"]), _extract, timeout=180),
        Tool("analyze_table", "Answer a question about the tables/sheets in a spreadsheet, CSV or document by running "
                              "pandas over all rows in the sandbox (sums, averages, group-bys, trends, filters).",
             obj({"document_id": doc_id, "question": {"type": "string"}}, ["document_id", "question"]), _analyze, timeout=240),
        Tool("generate_document", "Create a new DOCX, XLSX, PPTX, PDF or Markdown file in the user's documents, optionally "
                                  "grounded in source documents.",
             obj({"prompt": {"type": "string"}, "format": {"type": "string", "enum": ["docx", "xlsx", "pptx", "pdf", "md"]},
                  "source_ids": {"type": "array", "items": {"type": "string"}}, "filename": {"type": "string"}}, ["prompt"]),
             _generate, timeout=300),
    ]
