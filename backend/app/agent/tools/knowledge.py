"""Tools over the user's own data: uploaded documents and long-term memory."""
from __future__ import annotations

from ... import db
from ...llm import registry
from .base import Tool, ToolContext


async def _search_documents(args: dict, ctx: ToolContext) -> str:
    q = str(args["query"])
    emb = await registry.embed([q])
    hits = await db.search_chunks(ctx.user_id, q, emb[0] if emb else None,
                                  ctx.file_ids if args.get("only_attached", True) and ctx.file_ids else None, k=6)
    if not hits:
        return "No matching passages found in the user's documents."
    return "\n\n".join(f"[{h['filename']} · part {h['idx'] + 1}]\n{h['content']}" for h in hits)


async def _save_memory(args: dict, ctx: ToolContext) -> str:
    fact = str(args["fact"]).strip()[:500]
    emb = await registry.embed([fact])
    await db.add_memory(ctx.user_id, fact, emb[0] if emb else None, ctx.conversation_id)
    return f"Saved to memory: {fact}"


def make_knowledge_tools() -> list[Tool]:
    return [
        Tool(name="search_documents",
             description="Search the user's uploaded files for passages relevant to a query.",
             parameters={"type": "object", "properties": {
                 "query": {"type": "string"},
                 "only_attached": {"type": "boolean", "description": "Limit to files attached in this chat (default true)"}},
                 "required": ["query"]},
             run=_search_documents, timeout=30),
        Tool(name="save_memory",
             description="Save a durable fact or preference about the user for future conversations. Only when the "
                         "user shares something lasting about themselves or asks you to remember it.",
             parameters={"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
             run=_save_memory, timeout=20),
    ]
