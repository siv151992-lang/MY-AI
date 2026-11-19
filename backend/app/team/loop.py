"""One specialist working on one task: a tool loop that must end in a structured, grounded submission."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..agent.reasoning.privacy import strip_private
from ..agent.tools.base import ToolContext, ToolRegistry
from ..llm.formats import ProviderError, text_msg
from .specialists import AgentSpec

SUBMIT_RESULT = {
    "name": "submit_result",
    "description": "Finish your task. Report honestly: 'done' only if the task was actually achieved with evidence you saw.",
    "parameters": {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["done", "partial", "failed"]},
        "result": {"type": "string", "description": "Your full result in Markdown (findings, code, analysis or text)."},
        "sources": {"type": "array", "items": {"type": "string"},
                    "description": "URLs or document names you actually used (must come from your tool results)."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string", "description": "Caveats, gaps, disagreements between sources."}},
        "required": ["status", "result"]},
}
SUBMIT_VERDICT = {
    "name": "submit_verdict",
    "description": "Finish the verification with your verdict.",
    "parameters": {"type": "object", "properties": {
        "ok": {"type": "boolean", "description": "true only if the answer is correct, complete and supported"},
        "issues": {"type": "array", "items": {"type": "string"}, "description": "Specific problems that must be fixed"},
        "checked": {"type": "array", "items": {"type": "string"}, "description": "What you checked and how (tool used, result)"}},
        "required": ["ok", "issues", "checked"]},
}


@dataclass
class Outcome:
    status: str                     # done | partial | failed
    result: str = ""
    sources: list[str] = field(default_factory=list)
    confidence: str = "medium"
    notes: str = ""
    error: str = ""
    tool_calls: int = 0
    tool_log: list[dict] = field(default_factory=list)
    verdict: dict | None = None


Emit = Callable[[str, dict], Awaitable[None]]


def _summary(name: str, args: dict) -> str:
    for key in ("query", "url", "question", "command", "path", "text", "prompt", "document_id"):
        if key in args:
            return f"{name}: {str(args[key])[:120]}"
    if name == "run_python":
        return "run_python: " + str(args.get("code", "")).strip().split("\n")[0][:100]
    if name == "follow_link":
        return f"follow_link [{args.get('index')}]"
    return name


def ground_sources(sources: list, evidence: str) -> tuple[list[str], list[str]]:
    """Keep only sources that actually appear in this agent's tool results."""
    ev = evidence.lower()
    kept, dropped = [], []
    for s in sources or []:
        s = str(s).strip()
        if not s:
            continue
        key = re.sub(r"^https?://(www\.)?", "", s.lower()).rstrip("/")
        (kept if key and key[:200] in ev else dropped).append(s)
    return kept[:30], dropped


async def run_specialist(spec: AgentSpec, llm, model: str, prompt: str, tools: ToolRegistry, ctx: ToolContext,
                         emit: Emit, *, control: str = "submit_result", max_calls: int = 12, max_tokens: int = 4096,
                         reasoning: dict | None = None) -> Outcome:
    control_spec = SUBMIT_VERDICT if control == "submit_verdict" else SUBMIT_RESULT
    specs = tools.specs() + [control_spec]
    system = spec.system(control)
    convo = [text_msg("user", prompt)]
    evidence: list[str] = []
    log: list[dict] = []
    successful = 0
    nudged = rejected = False

    for round_ in range(max_calls + 1):
        last = round_ == max_calls
        if last:
            convo.append(text_msg("user", f"You have used your tool budget. Call {control} now with what you have."))
        calls, private, text = [], [], ""
        try:
            async for ev in llm.stream(model=model, system=system, messages=convo,
                                       tools=[control_spec] if last else specs, max_tokens=max_tokens, reasoning=reasoning):
                if ev["type"] == "text":
                    text += ev["text"]
                elif ev["type"] == "tool_call":
                    calls.append(ev)
                elif ev["type"] == "thinking_block":
                    private.append(ev["part"])
        except ProviderError as e:
            return Outcome("failed", error=f"Model error: {e}", tool_calls=successful, tool_log=log)
        text = strip_private(text).strip()
        if text:
            await emit("agent_note", {"text": text[:600]})
        if not calls:
            if nudged or last:
                return Outcome("failed", result=text[:4000], error=f"The agent stopped without calling {control}.",
                               tool_calls=successful, tool_log=log)
            nudged = True
            convo += [{"role": "assistant", "content": private + [{"type": "text", "text": text or "(no output)"}]},
                      text_msg("user", f"Continue the task with your tools, then call {control}.")]
            continue
        parts = private + ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_call", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
        results, finished = [], None
        for c in calls:
            if c["name"] == control:
                args = c["input"] or {}
                if control == "submit_verdict":
                    finished = Outcome("done", verdict={"ok": bool(args.get("ok")),
                                                        "issues": [str(i)[:500] for i in args.get("issues") or []][:15],
                                                        "checked": [str(i)[:300] for i in args.get("checked") or []][:20]},
                                       tool_calls=successful, tool_log=log)
                    results.append({"type": "tool_result", "tool_call_id": c["id"], "content": "Verdict recorded.", "is_error": False})
                    continue
                status = args.get("status") if args.get("status") in ("done", "partial", "failed") else "partial"
                if status == "done" and spec.requires_tools and successful == 0 and not rejected:
                    rejected = True
                    results.append({"type": "tool_result", "tool_call_id": c["id"], "is_error": True,
                                    "content": "Rejected: you have not obtained any evidence with your tools yet. Use them, "
                                               "or submit status 'failed' / 'partial' and explain why."})
                    continue
                kept, dropped = ground_sources(args.get("sources") or [], "\n".join(evidence))
                notes = str(args.get("notes", ""))[:2000]
                if dropped:
                    notes += (" " if notes else "") + (f"[{len(dropped)} listed source(s) were removed because they did "
                                                       f"not appear in this agent's tool results: {', '.join(dropped[:5])}]")
                if status == "done" and spec.requires_tools and successful == 0:
                    status, notes = "partial", notes + " [No tool evidence was gathered for this result.]"
                finished = Outcome(status, str(args.get("result", ""))[:30_000], kept,
                                   args.get("confidence") if args.get("confidence") in ("high", "medium", "low") else "medium",
                                   notes.strip(), tool_calls=successful, tool_log=log)
                results.append({"type": "tool_result", "tool_call_id": c["id"], "content": "Result submitted.", "is_error": False})
                continue
            await emit("agent_tool_start", {"tool": c["name"], "summary": _summary(c["name"], c["input"] or {})})
            out, is_err = await tools.execute(c["name"], c["input"] or {}, ctx)
            if not is_err:
                successful += 1
                evidence.append(out)
            log.append({"tool": c["name"], "input": json.dumps(c["input"], default=str)[:300], "ok": not is_err, "output": out[:1500]})
            await emit("agent_tool_end", {"tool": c["name"], "ok": not is_err, "output": out[:300]})
            results.append({"type": "tool_result", "tool_call_id": c["id"], "content": out, "is_error": is_err})
        if finished:
            return finished
        convo += [{"role": "assistant", "content": parts}, {"role": "user", "content": results}]
    return Outcome("failed", error="Tool budget exhausted without a submission.", tool_calls=successful, tool_log=log)
