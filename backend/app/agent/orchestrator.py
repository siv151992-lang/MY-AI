"""Agent orchestrator.

    understand (analyze) -> clarify? -> plan -> reason privately & act (tool loop) -> verify -> revise -> respond

Emits a stream of events consumed by the SSE endpoint:
  status, analysis, plan, token, tool_start, tool_end, verification, reset, final, error
Private reasoning (provider thinking blocks, scratchpads) never leaves this module except as opaque
thinking parts that are fed back to the same provider inside a tool loop.
No database or HTTP dependencies, so it is fully unit-testable with a scripted provider.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from ..llm.formats import ProviderError, text_msg
from .reasoning import ReasoningEngine
from .reasoning.privacy import SCRATCHPAD_INSTRUCTION, ScratchpadFilter, strip_private
from .tools.base import ToolContext, ToolRegistry


@dataclass
class AgentConfig:
    model: str
    max_tokens: int = 4096
    reasoning_mode: str = "scratchpad"   # anthropic_thinking | openai_effort | scratchpad | none
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


async def run_agent(*, llm, engine: ReasoningEngine, cfg: AgentConfig, build_system: Callable[[str], str],
                    history: list[dict], user_message: dict, tools: ToolRegistry, ctx: ToolContext,
                    state_text: str = "", last_assistant_asked: bool = False) -> AsyncIterator[dict]:
    user_text = " ".join(p["text"] for p in user_message["content"] if p["type"] == "text")
    tool_names = tools.names() if cfg.use_tools else []

    # 1-3. Understand, decide whether to clarify, plan
    yield {"type": "status", "text": "Understanding the request"}
    prep = await engine.prepare(user_text, history=history, state_text=state_text,
                                has_files=cfg.has_documents or any(p["type"] == "image" for p in user_message["content"]),
                                tool_names=tool_names, agent_mode=cfg.agent_mode,
                                last_assistant_asked=last_assistant_asked)
    analysis = prep.analysis
    public_analysis = None if analysis.fast_path else analysis.public()
    if public_analysis:
        yield {"type": "analysis", **public_analysis}

    if prep.clarify and prep.clarify.ask:
        msg = prep.clarify.message
        yield {"type": "token", "text": msg}
        yield {"type": "final", "content": msg, "meta": {
            "analysis": public_analysis, "clarification": True, "plan": None, "tools": [],
            "verification": None, "revisions": 0, "usage": {"input_tokens": 0, "output_tokens": 0},
            "stop_reason": "clarification"}}
        return

    steps = prep.plan.steps() if prep.plan and prep.plan.subtasks else None
    if steps:
        yield {"type": "plan", "steps": steps}

    guidance = prep.guidance
    if cfg.reasoning_mode == "scratchpad" and prep.effort != "none":
        guidance = f"{guidance}\n\n{SCRATCHPAD_INSTRUCTION}"
    system = build_system(guidance)
    reasoning = {"mode": cfg.reasoning_mode, "effort": prep.effort}
    tool_specs = tools.specs() if cfg.use_tools else None
    messages = [*history, user_message]
    run = _Run()

    revisions = 0
    last_verdict: dict | None = None
    while True:
        # 4. Reason (privately) and act
        async for ev in _act(llm, cfg, system, messages, tool_specs, tools, ctx, run, reasoning):
            yield ev
        if run.stop_reason == "error":
            return

        # 5. Verify important results
        if revisions < cfg.max_revisions and engine.should_verify(analysis, bool(run.tool_log), run.answer):
            yield {"type": "status", "text": "Checking the answer"}
            verdict = await engine.verify(analysis, user_text, run.answer, run.evidence)
            last_verdict = verdict.to_dict()
            yield {"type": "verification", **last_verdict}
            if not verdict.ok and verdict.all_issues():
                revisions += 1
                feedback = ("A reviewer checked your draft and found these problems:\n"
                            + "\n".join(f"- {i}" for i in verdict.all_issues())
                            + "\n\nWrite the complete corrected answer for the user now (use tools if needed). "
                              "Do not mention the review.")
                messages = [*messages, text_msg("assistant", run.answer), text_msg("user", feedback)]
                run.answer = ""
                yield {"type": "reset"}
                continue
        break

    yield {"type": "final", "content": strip_private(run.answer), "meta": {
        "analysis": public_analysis, "plan": steps, "tools": run.tool_log, "verification": last_verdict,
        "revisions": revisions, "usage": run.usage, "stop_reason": run.stop_reason, "effort": prep.effort}}


async def _stream_step(llm, cfg: AgentConfig, system: str, convo: list[dict], tool_specs, run: _Run,
                       reasoning: dict, calls: list[dict], private_parts: list[dict]) -> AsyncIterator[dict]:
    """One model call. Visible text is streamed; private reasoning is filtered or captured, never emitted."""
    filt = ScratchpadFilter() if cfg.reasoning_mode == "scratchpad" else None
    announced = False
    step_text = ""

    def visible(t: str) -> str:
        nonlocal step_text
        step_text += t
        run.answer += t
        return t

    async for ev in llm.stream(model=cfg.model, system=system, messages=convo, tools=tool_specs,
                               max_tokens=cfg.max_tokens, reasoning=reasoning):
        typ = ev["type"]
        if typ == "text":
            if filt:
                was_thinking = filt.thinking
                out = filt.feed(ev["text"])
                if filt.thinking and not was_thinking and not announced:
                    announced = True
                    yield {"type": "status", "text": "Thinking"}
                if out:
                    yield {"type": "token", "text": visible(out)}
            else:
                yield {"type": "token", "text": visible(ev["text"])}
        elif typ == "thinking_start":
            if not announced:
                announced = True
                yield {"type": "status", "text": "Thinking"}
        elif typ == "thinking_block":
            private_parts.append(ev["part"])
        elif typ == "tool_call":
            calls.append(ev)
        elif typ == "done":
            run.stop_reason = ev["stop_reason"]
            for k in run.usage:
                run.usage[k] += ev["usage"].get(k, 0)
    if filt:
        tail = filt.flush()
        if tail:
            yield {"type": "token", "text": visible(tail)}
    yield {"type": "_step_text", "text": step_text}


async def _act(llm, cfg: AgentConfig, system: str, messages: list[dict], tool_specs, tools: ToolRegistry,
               ctx: ToolContext, run: _Run, reasoning: dict) -> AsyncIterator[dict]:
    """Stream the model; execute tool calls; repeat until it answers or the step budget runs out."""
    convo = list(messages)
    for _ in range(cfg.max_steps):
        calls: list[dict] = []
        private_parts: list[dict] = []
        step_text = ""
        try:
            async for ev in _stream_step(llm, cfg, system, convo, tool_specs, run, reasoning, calls, private_parts):
                if ev["type"] == "_step_text":
                    step_text = ev["text"]
                else:
                    yield ev
        except ProviderError as e:
            run.stop_reason = "error"
            yield {"type": "error", "message": str(e), "partial": run.answer}
            return

        if not calls:
            return

        if step_text and not step_text.endswith("\n"):
            run.answer += "\n\n"
            yield {"type": "token", "text": "\n\n"}

        # Provider-native thinking must precede tool calls and be returned verbatim within the tool loop.
        assistant_parts = private_parts + ([{"type": "text", "text": step_text}] if step_text else []) + [
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

    yield {"type": "status", "text": "Wrapping up"}
    convo.append(text_msg("user", "You have reached the tool-use limit. Do not call any more tools. "
                                  "Give your best final answer now with what you have."))
    try:
        async for ev in _stream_step(llm, cfg, system, convo, tool_specs, run, reasoning, [], []):
            if ev["type"] != "_step_text":
                yield ev
    except ProviderError as e:
        run.stop_reason = "error"
        yield {"type": "error", "message": str(e), "partial": run.answer}
