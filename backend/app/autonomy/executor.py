"""Step executor: performs ONE plan step with real tool calls and reports a structured outcome.

Anti-pretending guarantees:
  * the step ends only through the `complete_step` control tool (structured status + summary);
  * a step whose plan requires a tool cannot be marked done unless a call to an action tool actually succeeded;
  * every tool call (including denied ones) is recorded as an ActionRecord — the report is built from those.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..agent.reasoning.privacy import strip_private
from ..agent.tools.base import ToolContext, ToolRegistry
from ..llm.formats import ProviderError, text_msg
from .models import ActionRecord, Step
from .permissions import ApprovalTimeout

COMPLETE_STEP = {
    "name": "complete_step",
    "description": "Call exactly once when the current step is finished or cannot be finished. Report honestly: "
                   "status 'done' only if the success criteria were actually met by tool results you saw.",
    "parameters": {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["done", "failed"]},
        "summary": {"type": "string", "description": "What was actually done and the concrete result (ids, numbers, links)."},
        "error": {"type": "string", "description": "If failed: what went wrong."}},
        "required": ["status", "summary"]},
}

EXECUTOR_SYSTEM = """You are the execution module of an autonomous agent. You are working on ONE step of a larger \
plan. Perform it for real using the tools — you have no other way to affect the world. Never claim an action \
happened unless a tool result in this conversation shows it. If a tool fails, read the error and try a sensible \
fix (different parameters, an alternative tool) before giving up. If the user denies permission for an action, do \
not retry it; finish the step as failed or find a permitted alternative.
When finished, call complete_step with an honest status and a concise factual summary (include ids, links, numbers).
Keep any prose brief; do not ask the user questions — they review approvals separately."""


@dataclass
class StepOutcome:
    status: str                        # done | failed
    summary: str = ""
    error: str = ""
    actions: list[ActionRecord] = field(default_factory=list)
    rejected: bool = False             # grounding guard refused a "done" claim without evidence


def _context_block(goal: str, plan: list[Step], step: Step, prior_errors: list[str]) -> str:
    lines = [f"<goal>\n{goal}\n</goal>", "<plan>"]
    for s in plan:
        marker = "→" if s.idx == step.idx else " "
        lines.append(f"{marker} {s.idx + 1}. [{s.status}] {s.title}")
    lines.append("</plan>")
    done = [s for s in plan if s.status == "done" and s.result]
    if done:
        lines.append("<results_of_completed_steps>")
        lines += [f"Step {s.idx + 1} ({s.title}): {s.result[:1500]}" for s in done[-6:]]
        lines.append("</results_of_completed_steps>")
    lines.append(f"<current_step>\nStep {step.idx + 1}: {step.title}\n{step.description}\n"
                 f"Expected tool: {step.tool}\nSuccess criteria: {step.success_criteria or 'step objective met'}\n</current_step>")
    if prior_errors:
        lines.append("<previous_attempts_failed>\n" + "\n".join(f"- {e}" for e in prior_errors) +
                     "\nUse a different approach this time.\n</previous_attempts_failed>")
    return "\n\n".join(lines)


class StepExecutor:
    def __init__(self, llm, model: str, tools: ToolRegistry, gate, bus, store, *, reasoning: dict | None = None,
                 max_calls: int = 10, max_tokens: int = 4096):
        self.llm, self.model, self.tools, self.gate, self.bus, self.store = llm, model, tools, gate, bus, store
        self.reasoning, self.max_calls, self.max_tokens = reasoning, max_calls, max_tokens

    async def execute(self, run_id: str, goal: str, plan: list[Step], step: Step, ctx: ToolContext,
                      prior_errors: list[str]) -> StepOutcome:
        specs = self.tools.specs() + [COMPLETE_STEP]
        convo = [text_msg("user", _context_block(goal, plan, step, prior_errors))]
        actions: list[ActionRecord] = []
        nudged = False

        for _ in range(self.max_calls):
            calls, private, text = [], [], ""
            try:
                async for ev in self.llm.stream(model=self.model, system=EXECUTOR_SYSTEM, messages=convo, tools=specs,
                                                max_tokens=self.max_tokens, reasoning=self.reasoning):
                    if ev["type"] == "text":
                        text += ev["text"]
                    elif ev["type"] == "tool_call":
                        calls.append(ev)
                    elif ev["type"] == "thinking_block":
                        private.append(ev["part"])
            except ProviderError as e:
                return StepOutcome("failed", error=f"Model error: {e}", actions=actions)

            text = strip_private(text)
            if text.strip():
                await self.bus.publish(run_id, "note", {"step": step.idx, "text": text.strip()[:1000]})

            if not calls:
                if nudged:
                    return StepOutcome("failed", summary=text[:2000],
                                       error="The model stopped without calling complete_step.", actions=actions)
                nudged = True
                convo += [{"role": "assistant", "content": private + [{"type": "text", "text": text or "(no output)"}]},
                          text_msg("user", "Continue the step using tools, then call complete_step.")]
                continue

            assistant_parts = private + ([{"type": "text", "text": text}] if text else []) + [
                {"type": "tool_call", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
            results = []
            finish: StepOutcome | None = None
            for c in calls:
                if c["name"] == "complete_step":
                    finish = self._finish(step, c["input"], actions)
                    results.append({"type": "tool_result", "tool_call_id": c["id"],
                                    "content": finish.error if finish.rejected else "Step closed.",
                                    "is_error": finish.rejected})
                    continue
                out, is_err = await self._call_tool(run_id, step, c, ctx, actions)
                results.append({"type": "tool_result", "tool_call_id": c["id"], "content": out, "is_error": is_err})
            convo += [{"role": "assistant", "content": assistant_parts}, {"role": "user", "content": results}]
            if finish:
                if finish.rejected:
                    # the grounding guard refused a "done" without evidence: let the model actually do it
                    await self.bus.publish(run_id, "warning", {"step": step.idx, "message": finish.error})
                    continue
                return finish

        return StepOutcome("failed", error=f"Step did not finish within {self.max_calls} model calls.", actions=actions)

    def _finish(self, step: Step, args: dict, actions: list[ActionRecord]) -> StepOutcome:
        status = "done" if str(args.get("status")) == "done" else "failed"
        summary = str(args.get("summary") or "")[:4000]
        if status == "done" and step.tool != "none" and not any(a.ok for a in actions):
            return StepOutcome("failed", summary=summary, actions=actions, rejected=True,
                               error=f"Rejected completion: this step requires the '{step.tool}' tool but no tool "
                                     f"action succeeded. Perform the action with a tool, or report the step as failed.")
        return StepOutcome(status, summary=summary, error=str(args.get("error") or "")[:2000], actions=actions)

    async def _call_tool(self, run_id: str, step: Step, call: dict, ctx: ToolContext,
                         actions: list[ActionRecord]) -> tuple[str, bool]:
        name, args = call["name"], call["input"]
        tool = self.tools.get(name)
        summary = tool.summary(args) if tool else name
        approval = None
        if tool and tool.needs_approval(args):
            await self.bus.publish(run_id, "status", {"status": "awaiting_approval"})
            try:
                ok = await self.gate.request(run_id, step.idx, name, args, summary)
            except ApprovalTimeout as e:
                ok, summary = False, f"{summary} (timed out: {e})"
            await self.bus.publish(run_id, "status", {"status": "running"})
            approval = "approved" if ok else "denied"
            if not ok:
                rec = ActionRecord(step.idx, name, summary, ok=False, output="User did not grant permission.",
                                   approval="denied")
                actions.append(rec)
                await self.store.add_action(run_id, rec)
                await self.bus.publish(run_id, "tool_end", {"step": step.idx, "tool": name, "ok": False,
                                                            "output": "Permission denied by user", "summary": summary})
                return "The user DENIED permission for this action. Do not retry it.", True

        await self.bus.publish(run_id, "tool_start", {"step": step.idx, "tool": name, "summary": summary})
        out, is_err = await self.tools.execute(name, args, ctx)
        rec = ActionRecord(step.idx, name, summary, ok=not is_err, output=out[:4000], approval=approval)
        actions.append(rec)
        await self.store.add_action(run_id, rec)
        await self.bus.publish(run_id, "tool_end", {"step": step.idx, "tool": name, "ok": not is_err,
                                                    "output": out[:1500], "summary": summary})
        return out, is_err
