"""The main orchestrator: plan → delegate (in parallel when safe) → reconcile conflicts → synthesize → verify."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..agent.jsonutil import extract_json
from ..agent.tools.base import Tool, ToolContext, ToolRegistry
from ..llm.formats import ProviderError, text_msg
from . import store
from .browser import BrowserSession, make_browser_tools
from .loop import Outcome, run_specialist
from .plan import PlanError, Task, parse_plan, ready, waves
from .specialists import DELEGATES, LABEL, SPECS, capabilities, registry_for

log = logging.getLogger("aria.team")

PLAN_SYSTEM = """You are the Planning Agent of a team of AI agents. Break the user's goal into the smallest set of \
tasks (1-8) that achieves it, and assign each task to the best agent:
{agents}

Rules:
- One task per distinct piece of work; a simple goal is ONE task.
- Write each instruction so the agent can work without seeing the others: include all relevant details from the goal.
- Use depends_on only when a task needs another task's output. Tasks without dependencies run in parallel, so keep \
independent work independent.
- When the user wants a written deliverable, end with a writing task that depends on the tasks producing its content.
- Do not add verification tasks: the orchestrator verifies the final answer automatically.
- Never assign an agent marked UNAVAILABLE. If the goal needs something no agent can do, plan what is possible.
Reply with JSON only:
{{"tasks": [{{"id": "T1", "agent": "research", "title": "short title", "instruction": "...", "depends_on": [], \
"expected_output": "what the result should contain"}}]}}"""

CONFLICT_SYSTEM = """Several AI agents worked on parts of one goal. Find places where their results CONTRADICT each \
other (different facts, numbers, dates, conclusions or recommendations about the same thing). Differences in scope or \
wording are not conflicts. For each conflict, say whether it can be settled by checking evidence (needs_check true for \
factual/numeric disagreements).
Reply with JSON only: {"conflicts": [{"topic": "...", "positions": [{"task": "T1", "claim": "..."}, {"task": "T3", \
"claim": "..."}], "needs_check": true}]}  (empty list if none)"""

SYNTH_SYSTEM = """You are the orchestrator of a team of AI agents. Combine the agents' results into the final response \
to the user's goal.
- Use only what the agents produced. Do not add facts, numbers or sources of your own.
- Where agents conflicted, use the recorded resolution and say briefly how it was settled; if a conflict is \
unresolved, present both positions and say it is unresolved.
- Keep citations: attribute facts to the URLs/documents the agents listed as sources.
- If a task failed or was only partial, say what is missing instead of papering over it.
- Write the response the user asked for (answer, report, code, analysis…), well structured in Markdown. Don't describe \
the agents' internal process except where it matters for trust (failures, conflicts, unverified points)."""

REVISE_SYSTEM = SYNTH_SYSTEM + "\n\nA verifier found problems with your previous draft. Fix every issue listed; if an issue " \
                               "cannot be fixed from the agents' results, state the limitation explicitly."

Publish = Callable[[str, dict], Awaitable]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    def __init__(self, *, run: dict, user_id: str, llm, model: str, utility_llm, utility_model: str,
                 tool_pool: Callable[[], dict[str, Tool]], publish: Publish, max_parallel: int = 3,
                 max_calls: int = 12, reasoning: dict | None = None):
        self.run, self.id, self.user_id = run, run["id"], user_id
        self.llm, self.model, self.util, self.util_model = llm, model, utility_llm, utility_model
        self.tool_pool, self._publish = tool_pool, publish
        self.max_parallel, self.max_calls, self.reasoning = max_parallel, max_calls, reasoning
        self.tasks: list[Task] = []
        self.conflicts: list[dict] = []
        self.ctx = ToolContext(user_id=user_id, conversation_id=self.id)

    async def emit(self, type_: str, **data) -> None:
        await self._publish(type_, data)

    async def status(self, status: str, text: str) -> None:
        await store.update_run(self.id, status=status)
        await self.emit("status", status=status, text=text)

    async def _complete(self, system: str, prompt: str, *, main: bool = False, max_tokens: int = 3000) -> str:
        llm, model = (self.llm, self.model) if main else (self.util, self.util_model)
        return await llm.complete(model=model, system=system, max_tokens=max_tokens, messages=[text_msg("user", prompt)])

    # ------------------------------------------------------------------ pipeline
    async def execute(self) -> tuple[str, str]:
        await self.plan()
        await self.delegate()
        if all(t.status == "failed" for t in self.tasks):
            raise RuntimeError("Every task failed, so there is no result to combine: "
                               + "; ".join(f"{t.id} ({LABEL[t.agent]}): {t.error}" for t in self.tasks)[:1500])
        await self.reconcile()
        final = await self.synthesize()
        return await self.verify(final)

    async def plan(self) -> None:
        await self.status("planning", "Planning Agent is breaking the goal into tasks")
        await self.emit("agent_status", agent="planning", state="working", task=None, title="Planning the work")
        pool = self.tool_pool()
        try:
            reply = await self._complete(PLAN_SYSTEM.format(agents=capabilities(pool)), f"Goal: {self.run['goal']}", max_tokens=2500)
            self.tasks, warnings = parse_plan(reply, set(DELEGATES))
        except (PlanError, ProviderError) as e:
            raise RuntimeError(f"Planning failed: {e}")
        await store.save_tasks(self.id, self.tasks)
        await store.update_run(self.id, plan_notes=warnings)
        await self.emit("plan", tasks=[t.to_dict() for t in self.tasks], waves=waves(self.tasks), warnings=warnings)
        await self.emit("agent_status", agent="planning", state="idle", task=None, title=f"Planned {len(self.tasks)} task(s)")

    def _prompt(self, t: Task) -> str:
        by_id = {x.id: x for x in self.tasks}
        parts = [f"Overall goal (for context): {self.run['goal']}", f"Your task ({t.id}): {t.title}\n{t.instruction}"]
        if t.expected_output:
            parts.append(f"Expected output: {t.expected_output}")
        for d in t.depends_on:
            dep = by_id[d]
            if dep.status == "done" or dep.result:
                parts.append(f"Input from {d} ({LABEL[dep.agent]}, status {dep.status}):\n{dep.result[:8000]}"
                             + (f"\nSources: {', '.join(dep.sources)}" if dep.sources else "")
                             + (f"\nNotes: {dep.notes}" if dep.notes else ""))
            else:
                parts.append(f"Input from {d} ({LABEL[dep.agent]}) is MISSING: that task failed ({dep.error}). "
                             "Work around it if you can and say what is missing.")
        if t.attempts > 1 and t.error:
            parts.append(f"Your previous attempt failed: {t.error}. Try a different approach.")
        return "\n\n".join(parts)

    async def _run_task(self, t: Task) -> None:
        spec = SPECS[t.agent]
        pool = dict(self.tool_pool())
        if t.agent == "browser":
            pool.update({tool.name: tool for tool in make_browser_tools(BrowserSession())})
        tools = registry_for(spec, pool)
        for attempt in (1, 2):
            t.attempts, t.status, t.started_at = attempt, "running", t.started_at or _now()
            await store.update_task(self.id, t)
            await self.emit("task_status", task=t.id, agent=t.agent, status="running", title=t.title, attempt=attempt)
            await self.emit("agent_status", agent=t.agent, state="working", task=t.id, title=t.title)

            async def emit(type_: str, data: dict, _t=t) -> None:
                await self._publish(type_, {**data, "task": _t.id, "agent": _t.agent})
            out: Outcome = await run_specialist(spec, self.llm, self.model, self._prompt(t), tools, self.ctx, emit,
                                                max_calls=self.max_calls, reasoning=self.reasoning)
            t.result, t.sources, t.confidence, t.notes, t.tool_calls = out.result, out.sources, out.confidence, out.notes, \
                t.tool_calls + out.tool_calls
            if out.status in ("done", "partial"):
                t.status, t.error = "done", ""
                if out.status == "partial":
                    t.notes = ("[partial result] " + t.notes).strip()
                break
            t.status, t.error = "failed", out.error or out.notes or "The agent reported failure."
            if attempt == 1:
                await self.emit("task_status", task=t.id, agent=t.agent, status="retrying", title=t.title, error=t.error[:300])
        t.finished_at = _now()
        await store.update_task(self.id, t)
        await self.emit("task_status", task=t.id, agent=t.agent, status=t.status, title=t.title, confidence=t.confidence,
                        sources=len(t.sources), error=t.error[:300], partial=t.notes.startswith("[partial"))
        await self.emit("agent_status", agent=t.agent, state="idle", task=t.id, title=t.title)

    async def delegate(self) -> None:
        """Run tasks as their dependencies finish. Side-effect-free agents run in parallel (up to max_parallel);
        an agent with side effects (creating files) runs alone."""
        await self.status("running", "Agents are working")
        running: dict[str, asyncio.Task] = {}
        exclusive = False
        while True:
            for t in ready(self.tasks):
                spec = SPECS[t.agent]
                if t.id in running:
                    continue
                if spec.side_effects:
                    if running:
                        continue
                    exclusive = True
                elif exclusive or len(running) >= self.max_parallel:
                    continue
                t.status = "running"
                running[t.id] = asyncio.create_task(self._run_task(t))
                if spec.side_effects:
                    break
            if not running:
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for tid in [k for k, v in running.items() if v in done]:
                exc = running.pop(tid).exception()
                task = next(x for x in self.tasks if x.id == tid)
                if exc:
                    log.exception("task %s crashed", tid, exc_info=exc)
                    task.status, task.error = "failed", f"Internal error: {type(exc).__name__}"
                    await store.update_task(self.id, task)
                    await self.emit("task_status", task=tid, agent=task.agent, status="failed", title=task.title, error=task.error)
                if SPECS[task.agent].side_effects:
                    exclusive = False

    async def reconcile(self) -> None:
        results = [t for t in self.tasks if t.status == "done" and t.result]
        if len(results) < 2:
            return
        await self.status("reconciling", "Comparing the agents' results for conflicts")
        listing = "\n\n".join(f"## {t.id} ({LABEL[t.agent]}): {t.title}\n{t.result[:5000]}" for t in results)
        try:
            data = extract_json(await self._complete(CONFLICT_SYSTEM, listing, max_tokens=1500))
        except ProviderError:
            return
        valid = {t.id for t in results}
        found = []
        for c in (data.get("conflicts") if isinstance(data, dict) else None) or []:
            if not isinstance(c, dict):
                continue
            pos = [{"task": str(p.get("task")), "claim": str(p.get("claim", ""))[:500]} for p in c.get("positions") or []
                   if isinstance(p, dict) and str(p.get("task")) in valid]
            if len({p["task"] for p in pos}) >= 2:
                found.append({"topic": str(c.get("topic", ""))[:200], "positions": pos, "needs_check": bool(c.get("needs_check")),
                              "resolution": "", "resolved_by": "", "status": "open"})
        self.conflicts = found[:6]
        await self.emit("conflicts", conflicts=self.conflicts)
        checks = [c for c in self.conflicts if c["needs_check"]][:3]
        await asyncio.gather(*(self._adjudicate(i, c) for i, c in enumerate(checks)))
        for c in self.conflicts:
            if c["status"] == "open":
                c.update(status="unresolved", resolution="Not independently checked; both positions are reported.")
        await store.update_run(self.id, conflicts=self.conflicts)
        await self.emit("conflicts", conflicts=self.conflicts)

    async def _adjudicate(self, i: int, c: dict) -> None:
        spec = SPECS["verification"]
        tools = registry_for(spec, self.tool_pool())
        tid = f"C{i + 1}"
        await self.emit("agent_status", agent="verification", state="working", task=tid, title=f"Settling conflict: {c['topic']}")
        by_id = {t.id: t for t in self.tasks}
        prompt = (f"Two or more agents disagree about: {c['topic']}\n\n"
                  + "\n".join(f"- {p['task']} ({LABEL[by_id[p['task']].agent]}) says: {p['claim']}"
                              + (f" (its sources: {', '.join(by_id[p['task']].sources[:5])})" if by_id[p['task']].sources else "")
                              for p in c["positions"])
                  + "\n\nDetermine which position is correct using your tools (check the sources, recompute numbers). In your "
                    "result state the resolution in one or two sentences first, then the evidence. If it cannot be settled, "
                    "say so and why (status 'partial').")

        async def emit(type_: str, data: dict) -> None:
            await self._publish(type_, {**data, "task": tid, "agent": "verification"})
        out = await run_specialist(spec, self.llm, self.model, prompt, tools, self.ctx, emit, max_calls=8, reasoning=self.reasoning)
        settled = out.status == "done" and out.tool_calls > 0
        c.update(resolution=out.result[:3000] or out.error, resolved_by="verification" if settled else "",
                 status="resolved" if settled else "unresolved", evidence=out.sources)
        await self.emit("conflict_resolved", index=self.conflicts.index(c), status=c["status"], resolution=c["resolution"][:600])
        await self.emit("agent_status", agent="verification", state="idle", task=tid, title=f"Conflict: {c['topic']}")

    def _material(self) -> str:
        parts = [f"User's goal: {self.run['goal']}"]
        for t in self.tasks:
            head = f"## {t.id} — {LABEL[t.agent]} — {t.title} [{t.status}{', ' + t.confidence + ' confidence' if t.confidence else ''}]"
            if t.status == "failed":
                parts.append(f"{head}\nFAILED: {t.error}" + (f"\nPartial output:\n{t.result[:3000]}" if t.result else ""))
            else:
                parts.append(f"{head}\n{t.result[:9000]}" + (f"\nSources: {', '.join(t.sources)}" if t.sources else "")
                             + (f"\nNotes: {t.notes}" if t.notes else ""))
        if self.conflicts:
            parts.append("## Conflicts between agents\n" + "\n".join(
                f"- {c['topic']}: " + " vs ".join(f"{p['task']}: {p['claim']}" for p in c["positions"])
                + f"\n  Resolution ({c['status']}{', checked by the Verification Agent' if c['resolved_by'] else ''}): {c['resolution'][:1500]}"
                for c in self.conflicts))
        return "\n\n".join(parts)

    async def synthesize(self, issues: list[str] | None = None, draft: str = "") -> str:
        await self.status("synthesizing", "Orchestrator is combining the results" if not issues else "Orchestrator is revising the answer")
        prompt = self._material()
        if issues:
            prompt += f"\n\n## Your previous draft\n{draft}\n\n## Problems found by the verifier\n" + "\n".join(f"- {i}" for i in issues)
        final = await self._complete(REVISE_SYSTEM if issues else SYNTH_SYSTEM, prompt[:150_000], main=True, max_tokens=6000)
        await self.emit("final_draft", revision=bool(issues), chars=len(final))
        return final.strip()

    async def verify(self, final: str) -> tuple[str, str]:
        verdict: dict | None = None
        for round_ in (1, 2):
            await self.status("verifying", "Verification Agent is checking the final answer")
            await self.emit("agent_status", agent="verification", state="working", task="final", title="Verifying the final answer")
            spec = SPECS["verification"]

            async def emit(type_: str, data: dict) -> None:
                await self._publish(type_, {**data, "task": "final", "agent": "verification"})
            prompt = (f"Check this final answer to the user's goal against the agents' results below. Verify the most "
                      f"important facts and numbers with your tools where possible. Flag anything unsupported by the agents' "
                      f"results, wrong, contradictory, missing from the goal, or overstated.\n\n# Final answer\n{final[:30000]}"
                      f"\n\n# Agents' results\n{self._material()[:60000]}")
            out = await run_specialist(spec, self.llm, self.model, prompt, registry_for(spec, self.tool_pool()), self.ctx,
                                       emit, control="submit_verdict", max_calls=8, reasoning=self.reasoning)
            await self.emit("agent_status", agent="verification", state="idle", task="final", title="Verified the final answer")
            if not out.verdict:
                verdict = {"ok": False, "issues": [f"Verification could not be completed: {out.error}"], "checked": [], "round": round_}
                break
            verdict = {**out.verdict, "round": round_, "tool_calls": out.tool_calls}
            await self.emit("verification", **verdict)
            if verdict["ok"] or not verdict["issues"] or round_ == 2:
                break
            final = await self.synthesize(verdict["issues"], final)
        await store.update_run(self.id, verification=verdict)
        ok = bool(verdict and verdict.get("ok"))
        return final, "completed" if ok else "completed_with_issues"
