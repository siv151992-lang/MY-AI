"""Agent orchestrator: plan -> reason/act (tool loop) -> verify -> (revise) -> respond.

Emits a stream of events consumed by the SSE endpoint:
  status, plan, token, tool_start, tool_end, verification, reset, final, error
Has no database or HTTP dependencies, so it is fully unit-testable with a scripted provider.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from ..llm.formats import ProviderError, text_msg
from . import planner, verifier
from .tools.base import ToolContext, ToolRegistry


@dataclass
class AgentConfig:
    model: str
    max_tokens: int = 4096
    utility_model: str | None = None
    use_tools: bool = True
    max_steps: int = 8
    max_revisions: int = 1
    agent_mode: bool = False
    has_documents: bool = False


@dataclass
class _Run:
    answer: str = ""
    tool_log: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    stop_reason: str = ""


async def run_agent(*, llm, utility_llm=None, cfg: AgentConfig, build_system: Callable[[list[str] | None], str],
                    history: list[dict], user_message: dict, tools: ToolRegistry, ctx: ToolContext
                    ) -> AsyncIterator[dict]:
    utility_llm = utility_llm or llm
    umodel = cfg.utility_model or cfg.model
    user_text = " ".join(p["text"] for p in user_message["content"] if p["type"] == "text")

    # 1. Planning engine
    plan: list[str] | None = None
    if planner.needs_plan(user_text, cfg.agent_mode, cfg.has_documents):
        yield {"type": "status", "text": "Planning"}
        try:
            plan = await planner.make_plan(utility_llm, umodel, user_text,
                                           context_hint="Tools available: " + ", ".join(tools.names()))
        except ProviderError:
            plan = None
        if plan:
            yield {"type": "plan", "steps": plan}

    system = build_system(plan)
    tool_specs = tools.specs() if cfg.use_tools else None
    messages = [*history, user_message]
    run = _Run()

    revisions = 0
    last_verdict: dict | None = None
    while True:
        # 2. Reasoning + tool loop
        async for ev in _act(llm, cfg, system, messages, tool_specs, tools, ctx, run):
            yield ev
        if run.stop_reason == "error":
            return

        # 3. Verification engine
        used_tools = bool(run.tool_log)
        if revisions < cfg.max_revisions and verifier.should_verify(used_tools, bool(plan), run.answer):
            yield {"type": "status", "text": "Checking the answer"}
            try:
                verdict = await verifier.verify(utility_llm, umodel, user_text, run.answer, run.evidence)
            except ProviderError:
                verdict = {"ok": True, "issues": [], "note": "verifier unavailable"}
            last_verdict = verdict
            yield {"type": "verification", **verdict}
            if not verdict["ok"] and verdict["issues"]:
                revisions += 1
                feedback = ("A reviewer checked your draft and found these problems:\n"
                            + "\n".join(f"- {i}" for i in verdict["issues"])
                            + "\n\nWrite the complete corrected answer for the user now (use tools if needed). "
                              "Do not mention the review.")
                messages = [*messages, text_msg("assistant", run.answer), text_msg("user", feedback)]
                run.answer = ""
                yield {"type": "reset"}
                continue
        break

    yield {"type": "final", "content": run.answer, "meta": {
        "plan": plan, "tools": run.tool_log, "verification": last_verdict,
        "revisions": revisions, "usage": run.usage, "stop_reason": run.stop_reason}}


async def _act(llm, cfg: AgentConfig, system: str, messages: list[dict], tool_specs, tools: ToolRegistry,
               ctx: ToolContext, run: _Run) -> AsyncIterator[dict]:
    """Stream the model; execute any tool calls; repeat until it answers or the step budget runs out."""
    convo = list(messages)
    for step in range(cfg.max_steps):
        calls: list[dict] = []
        step_text = ""
        try:
            async for ev in llm.stream(model=cfg.model, system=system, messages=convo, tools=tool_specs,
                                       max_tokens=cfg.max_tokens):
                if ev["type"] == "text":
                    step_text += ev["text"]
                    run.answer += ev["text"]
                    yield {"type": "token", "text": ev["text"]}
                elif ev["type"] == "tool_call":
                    calls.append(ev)
                elif ev["type"] == "done":
                    run.stop_reason = ev["stop_reason"]
                    for k in run.usage:
                        run.usage[k] += ev["usage"].get(k, 0)
        except ProviderError as e:
            run.stop_reason = "error"
            yield {"type": "error", "message": str(e), "partial": run.answer}
            return

        if not calls:
            return

        # keep streamed prose and the next step visually separated
        if step_text and not step_text.endswith("\n"):
            run.answer += "\n\n"
            yield {"type": "token", "text": "\n\n"}

        assistant_parts = ([{"type": "text", "text": step_text}] if step_text else []) + [
            {"type": "tool_call", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
        result_parts = []
        for c in calls:
            yield {"type": "tool_start", "id": c["id"], "name": c["name"], "input": c["input"]}
            output, is_error = await tools.execute(c["name"], c["input"], ctx)
            run.tool_log.append({"id": c["id"], "name": c["name"], "input": c["input"],
                                 "output": output[:2000], "is_error": is_error})
            run.evidence.append(f"[{c['name']} {json.dumps(c['input'])[:200]}]\n{output}")
            yield {"type": "tool_end", "id": c["id"], "name": c["name"], "output": output[:2000], "is_error": is_error}
            result_parts.append({"type": "tool_result", "tool_call_id": c["id"], "content": output, "is_error": is_error})
        convo += [{"role": "assistant", "content": assistant_parts}, {"role": "user", "content": result_parts}]

    # Step budget exhausted: ask for a final answer without tools.
    yield {"type": "status", "text": "Wrapping up"}
    convo.append(text_msg("user", "You have reached the tool-use limit. Do not call any more tools. "
                                  "Give your best final answer now with what you have."))
    try:
        # tools stay declared (some APIs reject tool blocks in history without them); further calls are ignored
        async for ev in llm.stream(model=cfg.model, system=system, messages=convo, tools=tool_specs,
                                   max_tokens=cfg.max_tokens):
            if ev["type"] == "text":
                run.answer += ev["text"]
                yield {"type": "token", "text": ev["text"]}
            elif ev["type"] == "done":
                run.stop_reason = ev["stop_reason"]
    except ProviderError as e:
        run.stop_reason = "error"
        yield {"type": "error", "message": str(e), "partial": run.answer}
