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
    """Neutral user message (text + images) and document descriptors for the system prompt.
    Attachments go through the document access check: ids the user can't read are dropped."""
    from ..documents import access, store as doc_store
    files = [await doc_store.get_doc(user_id, i) for i in await access.filter_readable(user_id, file_ids)]
    parts: list[dict] = []
    docs: list[dict] = []
    for f in files:
        if f["kind"] == "image":
            if vision:
                data = await asyncio.to_thread(Path(f["storage_path"]).read_bytes)
                parts.append({"type": "image", "media_type": f["mime_type"], "data": base64.b64encode(data).decode()})
            elif f.get("text_content"):
                docs.append({"filename": f["filename"], "full_text": "(Text and description extracted from the image; "
                                                                    "the selected model cannot see images.)\n" + f["text_content"][:FULL_TEXT_LIMIT]})
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


async def _attachment_kinds(user_id: str, file_ids: list[str]) -> set[str]:
    if not file_ids:
        return set()
    from ..documents import access, store as doc_store
    kinds = set()
    for i in await access.filter_readable(user_id, file_ids):
        d = await doc_store.get_doc(user_id, i)
        if d:
            kinds.add(d["kind"])
    return kinds


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


async def merge_events(agen: AsyncIterator[dict], queue: asyncio.Queue | None) -> AsyncIterator[dict]:
    """Interleave an agent's event stream with side events pushed by tools while they run
    (approval requests, file changes), so the client sees them without waiting for the tool to return."""
    if queue is None:
        async for ev in agen:
            yield ev
        return
    nxt = asyncio.ensure_future(agen.__anext__())
    getq = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({nxt, getq}, return_when=asyncio.FIRST_COMPLETED)
            if getq in done:
                yield getq.result()
                getq = asyncio.ensure_future(queue.get())
            if nxt in done:
                try:
                    ev = nxt.result()
                except StopAsyncIteration:
                    break
                yield ev
                nxt = asyncio.ensure_future(agen.__anext__())
        while not queue.empty():
            yield queue.get_nowait()
    finally:
        getq.cancel()
        if not nxt.done():
            nxt.cancel()
            try:
                await nxt
            except BaseException:  # noqa: BLE001 - cancelled or finished with an error; either way we are closing
                pass
        await agen.aclose()


async def respond(*, user: dict, conv: dict, text: str, file_ids: list[str], model_id: str | None,
                  agent_mode: bool, regenerate: bool, tools: ToolRegistry | None = None,
                  extra_system: list[str] | None = None, side_events: asyncio.Queue | None = None,
                  max_steps: int | None = None, extra_meta: dict | None = None,
                  feature: str = "chat", skill_id: str | None = None) -> AsyncIterator[dict]:
    """One chat turn. The coding workspace passes its own tools, prompt sections and a side-event queue.

    model_id "auto" (or a conversation set to auto) lets the adaptive router pick the model per message and fall back
    to the next best model when a provider fails before answering. Project, custom-agent and skill context are added
    by the platform hub."""
    from ..hub import context as hub_context, routing, usage
    usage.set_scope(user["id"], feature)
    s = get_settings()
    wanted = model_id or conv.get("model_id")
    registry.get_model(None)  # raises RuntimeError when no model is configured at all
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

    hub = await hub_context.for_turn(user["id"], conv, skill_id)
    for err in hub.errors:
        yield {"type": "status", "text": err}
    if hub.model_id and wanted in (None, "auto") or hub.force_model:
        wanted = hub.model_id or wanted
    route = None
    if wanted == "auto":
        kinds = await _attachment_kinds(user["id"], file_ids)
        route = await routing.route_for_user(user["id"], text, has_images="image" in kinds, has_documents="document" in kinds)
        if not route.model_id:
            yield {"type": "error", "message": "No connected model can handle this request: " + "; ".join(route.reasons[-1:])}
            return
        agent_mode = agent_mode or route.agent_mode
        yield {"type": "route", **route.public()}
        candidates = [registry.get_model(route.model_id)] + [registry.get_model(f) for f in route.fallbacks]
    else:
        candidates = [registry.get_model(wanted)]
    spec = candidates[0]
    if (("auto" if route else spec.id) != conv.get("model_id")) and not hub.force_model:
        await db.update_conversation(user["id"], conv["id"], model_id="auto" if route else spec.id)
    if hub.sections:
        extra_system = list(extra_system or []) + hub.sections
    from ..memory import store as memory_store
    memory_store.PROJECT_SCOPE.set(hub.project_id)  # context-aware memory: this project's + global memories only

    user_msg, docs = await _user_content(user["id"], text, file_ids, spec.vision)
    budget = Budget(history=s.context_token_budget)
    if memory.plan_needs_compaction(conv, prior, budget.history):
        yield {"type": "status", "text": "Condensing earlier conversation"}
    history, summary, compacted = await memory.prepare_history(user["id"], conv, prior, budget.history)
    recall = await memory.recall(user["id"], conv, text, budget)
    mem_public = recall.public()
    if mem_public["memories"] or mem_public["recalled"] or compacted:
        yield {"type": "memory_context", **mem_public, "compacted": compacted}
    tools = tools or build_tools()
    tools = hub.filter_tools(tools)
    state = conv.get("state") or {}
    state_text = render_state(state)
    last_asked = bool(prior and prior[-1]["role"] == "assistant" and (prior[-1].get("meta") or {}).get("clarification"))

    def build_system(guidance: str) -> str:
        return build_system_prompt(user_name=user.get("name", ""), memories=recall.memories,
                                   recalled=recall.recalled, conversation_summary=summary,
                                   conversation_state=state_text, documents=docs, reasoning_guidance=guidance,
                                   tool_names=tools.names() if spec.tools else [], extra_sections=extra_system)

    engine = ReasoningEngine.default(registry.provider_for(uspec), uspec.model)
    ctx = ToolContext(user_id=user["id"], conversation_id=conv["id"], file_ids=file_ids)
    fallbacks_used: list[dict] = []

    final = None
    partial = ""
    for attempt, spec in enumerate(candidates):
        llm = registry.provider_for(spec)
        cfg = AgentConfig(model=spec.model, max_tokens=spec.max_tokens, reasoning_mode=spec.reasoning,
                          use_tools=spec.tools, max_steps=max_steps or s.max_agent_steps, max_revisions=s.max_revisions,
                          agent_mode=agent_mode, has_documents=bool(docs))
        acted = retry = False
        try:
            async for ev in merge_events(run_agent(llm=llm, engine=engine, cfg=cfg, build_system=build_system,
                                                   history=history, user_message=user_msg, tools=tools, ctx=ctx,
                                                   state_text=state_text, last_assistant_asked=last_asked),
                                         side_events):
                if ev["type"] == "token":
                    partial += ev["text"]
                elif ev["type"] == "reset":
                    partial = ""
                elif ev["type"] == "tool_start":
                    acted = True
                if ev["type"] == "final":
                    final = ev
                    continue
                if (ev["type"] == "error" and route is not None and attempt + 1 < len(candidates)
                        and not partial.strip() and not acted):
                    # The provider failed before producing anything: nothing to undo, so try the next best model.
                    retry = True
                    fallbacks_used.append({"model": spec.id, "error": str(ev.get("message", ""))[:300]})
                    break
                yield ev
        finally:
            if final is None and partial.strip():
                # Client pressed stop / disconnected, or the provider failed mid-answer: keep what was produced.
                _spawn(db.add_message(conv["id"], "assistant", partial, [],
                                      {"interrupted": True, "model": spec.id, **(extra_meta or {})}))
        if not retry:
            break
        yield {"type": "route_fallback", "from": spec.id, "to": candidates[attempt + 1].id,
               "reason": fallbacks_used[-1]["error"]}

    if final is None or not final["content"].strip():
        if final is not None:
            yield {"type": "error", "message": "The model returned an empty response. Try again or switch models."}
        return
    final["meta"]["model"] = spec.id
    if route is not None:
        final["meta"]["route"] = {**route.public(), "model_id": spec.id, "fallbacks_used": fallbacks_used}
    if hub.meta:
        final["meta"].update(hub.meta)
    final["meta"].update(extra_meta or {})
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
