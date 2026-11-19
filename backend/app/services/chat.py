"""Chat service: assembles context (memory, history, files), runs the agent, persists results."""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import AsyncIterator

from .. import db
from ..agent.orchestrator import AgentConfig, run_agent
from ..agent.prompts import build_system_prompt
from ..agent.reasoning import ReasoningEngine
from ..agent.reasoning.context import render_state, update_state
from ..agent.tools.base import ToolContext, ToolRegistry
from ..agent.tools.code import make_code_tool
from ..agent.tools.knowledge import make_knowledge_tools
from ..agent.tools.web import make_web_tools
from ..config import get_settings
from ..llm import registry
from ..llm.formats import text_msg
from ..memory import service as memory
from ..memory.context import Budget
from .documents import FULL_TEXT_LIMIT

log = logging.getLogger("aria.chat")
_background: set[asyncio.Task] = set()


def build_tools() -> ToolRegistry:
    s = get_settings()
    reg = ToolRegistry(make_web_tools(s.tavily_api_key, s.brave_api_key) + make_knowledge_tools())
    if s.sandbox_url:
        reg.add(make_code_tool(s.sandbox_url, s.sandbox_token))
    return reg


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


async def _user_content(user_id: str, text: str, file_ids: list[str], vision: bool) -> tuple[dict, list[dict]]:
    """Neutral user message (text + images) and document descriptors for the system prompt."""
    files = await db.get_files(user_id, file_ids)
    parts: list[dict] = []
    docs: list[dict] = []
    for f in files:
        if f["kind"] == "image":
            if vision:
                data = await asyncio.to_thread(Path(f["storage_path"]).read_bytes)
                parts.append({"type": "image", "media_type": f["mime_type"], "data": base64.b64encode(data).decode()})
            else:
                parts.append({"type": "text", "text": f"[Image '{f['filename']}' attached, but the selected model "
                                                      f"cannot see images. Tell the user to switch to a vision model.]"})
        else:
            txt = f.get("text_content") or ""
            if len(txt) <= FULL_TEXT_LIMIT:
                docs.append({"filename": f["filename"], "full_text": txt})
            else:
                docs.append({"filename": f["filename"], "excerpt": txt[:4000] + "\n…"})
    parts.append({"type": "text", "text": text})
    return {"role": "user", "content": parts}, docs


async def _title(user_id: str, conv_id: str, user_text: str) -> str | None:
    try:
        spec = registry.utility_model()
        t = await registry.provider_for(spec).complete(
            model=spec.model, max_tokens=30,
            system="Write a 3-6 word title for a chat that starts with this message. Reply with the title only, no quotes.",
            messages=[text_msg("user", user_text[:2000])])
        title = t.strip().strip('"').strip()[:80] or None
        if title:
            await db.update_conversation(user_id, conv_id, title=title)
        return title
    except Exception:
        log.exception("title generation failed")
        return None


async def respond(*, user: dict, conv: dict, text: str, file_ids: list[str], model_id: str | None,
                  agent_mode: bool, regenerate: bool) -> AsyncIterator[dict]:
    s = get_settings()
    spec = registry.get_model(model_id or conv.get("model_id"))
    llm = registry.provider_for(spec)
    uspec = registry.utility_model()

    if regenerate:
        await db.delete_last_assistant(conv["id"])
        prior = await db.list_messages(conv["id"])
        if not prior or prior[-1]["role"] != "user":
            yield {"type": "error", "message": "Nothing to regenerate."}
            return
        last = prior.pop()
        text, file_ids = last["content"], last["file_ids"]
    else:
        prior = await db.list_messages(conv["id"])
        saved = await db.add_message(conv["id"], "user", text, file_ids)
        yield {"type": "user_saved", "message": saved}

    if spec.id != conv.get("model_id"):
        await db.update_conversation(user["id"], conv["id"], model_id=spec.id)

    user_msg, docs = await _user_content(user["id"], text, file_ids, spec.vision)
    budget = Budget(history=s.context_token_budget)
    if memory.plan_needs_compaction(conv, prior, budget.history):
        yield {"type": "status", "text": "Condensing earlier conversation"}
    history, summary, compacted = await memory.prepare_history(user["id"], conv, prior, budget.history)
    recall = await memory.recall(user["id"], conv, text, budget)
    mem_public = recall.public()
    if mem_public["memories"] or mem_public["recalled"] or compacted:
        yield {"type": "memory_context", **mem_public, "compacted": compacted}
    tools = build_tools()
    state = conv.get("state") or {}
    state_text = render_state(state)
    last_asked = bool(prior and prior[-1]["role"] == "assistant" and (prior[-1].get("meta") or {}).get("clarification"))

    def build_system(guidance: str) -> str:
        return build_system_prompt(user_name=user.get("name", ""), memories=recall.memories,
                                   recalled=recall.recalled, conversation_summary=summary,
                                   conversation_state=state_text, documents=docs, reasoning_guidance=guidance,
                                   tool_names=tools.names() if spec.tools else [])

    engine = ReasoningEngine.default(registry.provider_for(uspec), uspec.model)
    cfg = AgentConfig(model=spec.model, max_tokens=spec.max_tokens, reasoning_mode=spec.reasoning,
                      use_tools=spec.tools, max_steps=s.max_agent_steps, max_revisions=s.max_revisions,
                      agent_mode=agent_mode, has_documents=bool(docs))
    ctx = ToolContext(user_id=user["id"], conversation_id=conv["id"], file_ids=file_ids)

    final = None
    partial = ""
    try:
        async for ev in run_agent(llm=llm, engine=engine, cfg=cfg, build_system=build_system, history=history,
                                  user_message=user_msg, tools=tools, ctx=ctx, state_text=state_text,
                                  last_assistant_asked=last_asked):
            if ev["type"] == "token":
                partial += ev["text"]
            elif ev["type"] == "reset":
                partial = ""
            if ev["type"] == "final":
                final = ev
                continue
            yield ev
    finally:
        if final is None and partial.strip():
            # Client pressed stop / disconnected, or the provider failed mid-answer: keep what was produced.
            _spawn(db.add_message(conv["id"], "assistant", partial, [],
                                  {"interrupted": True, "model": spec.id}))

    if final is None or not final["content"].strip():
        if final is not None:
            yield {"type": "error", "message": "The model returned an empty response. Try again or switch models."}
        return
    final["meta"]["model"] = spec.id
    final["meta"]["memory"] = {"ids": [m["id"] for m in recall.memories], "recalled": len(recall.recalled),
                               "compacted": compacted}  # ids only: deleting a memory leaves no copy behind
    msg = await db.add_message(conv["id"], "assistant", final["content"], [], final["meta"])
    yield {"type": "done", "message": msg}
    if not prior and not regenerate:
        title = await _title(user["id"], conv["id"], text)
        if title:
            yield {"type": "title", "title": title}
    _spawn(_update_state(user["id"], conv["id"], state, text, final["content"]))
    # Learn from the exchange. Suggestions are streamed so the user can approve them inline; if the client has
    # gone, the task still finishes and the suggestions wait in the Memory panel.
    learn = asyncio.create_task(memory.propose_from_exchange(user["id"], conv, text, final["content"]))
    _background.add(learn)
    learn.add_done_callback(_background.discard)
    try:
        learned = await asyncio.wait_for(asyncio.shield(learn), timeout=30)
    except asyncio.TimeoutError:
        learned = []
    if learned:
        yield {"type": "memory_suggestions", "items": [
            {"id": m["id"], "content": m["content"], "category": m["category"], "status": m["status"],
             "importance": m["importance"], "supersedes_id": m.get("supersedes_id")} for m in learned]}


async def _update_state(user_id: str, conv_id: str, state: dict, user_text: str, answer: str) -> None:
    """Keep the conversation's structured working memory current (long-conversation coherence)."""
    try:
        spec = registry.utility_model()
        new_state = await update_state(registry.provider_for(spec), spec.model, state, user_text, answer)
        await db.update_conversation(user_id, conv_id, state=new_state)
    except Exception:
        log.exception("conversation state update failed")
