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


async def _conversation(ctx: ToolContext) -> dict | None:
    """The chat this tool runs in (None for autonomous runs, whose context id is a run id)."""
    try:
        return await db.get_conversation(ctx.user_id, ctx.conversation_id) if ctx.conversation_id else None
    except Exception:
        return None


async def _memory_allowed(ctx: ToolContext) -> str | None:
    conv = await _conversation(ctx)
    if conv is not None and not conv.get("memory_enabled", True):
        return "Memory is turned off for this conversation, so nothing was saved or read. Tell the user."
    return None


async def _save_memory(args: dict, ctx: ToolContext) -> str:
    from ...memory import service
    conv = await _conversation(ctx)
    if conv is not None and not conv.get("memory_enabled", True):
        return "Memory is turned off for this conversation, so nothing was saved. Tell the user."
    try:
        m = await service.create(ctx.user_id, str(args["fact"]), source="explicit", category=args.get("category"),
                                 conv_id=conv["id"] if conv else None, changed_by="assistant")
    except service.MemoryRejected as e:
        return f"Not saved: {e}"
    if m.get("duplicate"):
        return f"Already remembered: {m['content']}"
    return f"Saved to memory [{m['id']}]: {m['content']}"


async def _search_memory(args: dict, ctx: ToolContext) -> str:
    from ...memory import service, store
    from ...memory.scoring import fuse_and_rank
    q = str(args["query"])
    scope = args.get("scope", "all")
    out: list[str] = []
    emb = await registry.embed([q]) if q else None
    if scope in ("all", "memories"):
        settings = await service.get_settings(ctx.user_id)
        if settings.memory_enabled and not await _memory_allowed(ctx):
            for m in await service.search(ctx.user_id, q, k=8, embedding=emb[0] if emb else None):
                out.append(f"[memory {m['id']} · {m['category']}] {m['content']}")
    if scope in ("all", "conversation"):
        vh, kh = await store.search_segments(ctx.user_id, q, emb[0] if emb else None,
                                             conv_id=ctx.conversation_id, include_other=False, k=4)
        for s in fuse_and_rank(vh, kh, min_similarity=0.3)[:4]:
            out.append(f"[earlier in this chat, messages {s['from_seq']}-{s['to_seq']}]\n{s['content']}")
    return "\n\n".join(out) or "Nothing relevant found in memory."


async def _forget_memory(args: dict, ctx: ToolContext) -> str:
    from ...memory import service
    try:
        await service.delete(ctx.user_id, str(args["memory_id"]))
    except service.MemoryNotFound:
        return "No memory with that id. Use search_memory to find the right one."
    return "Deleted that memory permanently."


async def _update_memory(args: dict, ctx: ToolContext) -> str:
    from ...memory import service
    try:
        m = await service.update(ctx.user_id, str(args["memory_id"]), {"content": str(args["content"])},
                                 changed_by="assistant", reason="corrected at the user's request")
    except service.MemoryNotFound:
        return "No memory with that id. Use search_memory to find the right one."
    except service.MemoryRejected as e:
        return f"Not updated: {e}"
    return f"Memory updated: {m['content']}"


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
             description="Save a durable fact, preference or standing instruction about the user for future "
                         "conversations. Use only when the user explicitly asks you to remember something. "
                         "Facts you notice on your own are proposed to the user automatically; do not save those.",
             parameters={"type": "object", "properties": {
                 "fact": {"type": "string", "description": "One short third-person sentence"},
                 "category": {"type": "string", "enum": ["instruction", "profile", "preference", "project",
                                                         "relationship", "fact", "other"]}},
                 "required": ["fact"]},
             run=_save_memory, timeout=20),
        Tool(name="search_memory",
             description="Search what you remember: the user's long-term memories and/or summaries of earlier, "
                         "compacted parts of this conversation. Returns memory ids you can pass to update_memory "
                         "or forget_memory.",
             parameters={"type": "object", "properties": {
                 "query": {"type": "string"},
                 "scope": {"type": "string", "enum": ["all", "memories", "conversation"]}},
                 "required": ["query"]},
             run=_search_memory, timeout=20),
        Tool(name="update_memory",
             description="Correct a stored memory when the user says it is wrong or out of date.",
             parameters={"type": "object", "properties": {
                 "memory_id": {"type": "string"}, "content": {"type": "string"}},
                 "required": ["memory_id", "content"]},
             run=_update_memory, timeout=20),
        Tool(name="forget_memory",
             description="Permanently delete a stored memory. Only when the user explicitly asks you to forget it.",
             parameters={"type": "object", "properties": {"memory_id": {"type": "string"}},
                         "required": ["memory_id"]},
             run=_forget_memory, timeout=20),
    ]
