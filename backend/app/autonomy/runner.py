"""AgentRunner: drives one autonomous run end to end.

    plan -> for each step: execute (with retries) -> on failure re-plan the remainder
         -> verify the goal against real tool actions -> remediate once if needed -> report
"""
from __future__ import annotations

import asyncio
import logging

from ..agent.tools.base import ToolContext, ToolRegistry
from ..llm.formats import ProviderError
from .executor import StepExecutor
from .models import TERMINAL, Run, Step, now
from .planner import GoalPlanner
from .report import build_report, verify_goal, write_summary

log = logging.getLogger("aria.autonomy")


class AgentRunner:
    def __init__(self, *, store, bus, gate, llm, model: str, utility_llm, utility_model: str, tools: ToolRegistry,
                 reasoning: dict | None = None, max_plan_steps: int = 12, step_retries: int = 2, max_replans: int = 2,
                 calls_per_step: int = 10, max_tokens: int = 4096, backoff_base: float = 1.0):
        self.store, self.bus, self.gate, self.tools = store, bus, gate, tools
        self.utility_llm, self.utility_model = utility_llm, utility_model
        self.planner = GoalPlanner(utility_llm, utility_model, max_plan_steps)
        self.executor = StepExecutor(llm, model, tools, gate, bus, store, reasoning=reasoning,
                                     max_calls=calls_per_step, max_tokens=max_tokens)
        self.step_retries, self.max_replans = step_retries, max_replans
        self.errors: list[dict] = []
        self.backoff_base = backoff_base

    async def _status(self, run: Run, status: str, **extra) -> None:
        run.status = status
        await self.store.update_run(run)
        await self.bus.publish(run.id, "status", {"status": status, **extra})

    async def _save_step(self, run: Run, step: Step, event: str, **extra) -> None:
        await self.store.update_step(run.id, step)
        await self.bus.publish(run.id, event, {"step": step.to_dict(), **extra})

    async def run(self, run: Run) -> Run:
        ctx = ToolContext(user_id=run.user_id, conversation_id=run.id)
        catalog = self.tools.catalog()
        steps: list[Step] = []
        verification = None
        try:
            # 1. Plan
            await self._status(run, "planning")
            steps = await self.store.replace_steps(run.id, await self.planner.plan(run.goal, catalog))
            await self.bus.publish(run.id, "plan", {"steps": [s.to_dict() for s in steps]})

            # 2. Execute, recover, verify, remediate
            await self._status(run, "running")
            replans, remediated = 0, False
            i = 0
            while True:
                while i < len(steps):
                    step = steps[i]
                    ok = await self._run_step(run, steps, step, ctx)
                    if ok:
                        i += 1
                        continue
                    # 3. Recovery: re-plan the remainder around the failure
                    if replans >= self.max_replans:
                        for s in steps[i + 1:]:
                            s.status = "skipped"
                            await self._save_step(run, s, "step_skipped")
                        run.error = f"Step {step.idx + 1} failed and the re-planning budget is exhausted."
                        break
                    replans += 1
                    await self.bus.publish(run.id, "replanning", {"after_step": step.idx, "attempt": replans})
                    try:
                        new = await self.planner.replan(run.goal, catalog, steps[:i], step)
                    except ProviderError as e:
                        new = []
                        log.warning("replan failed: %s", e)
                    if self.errors:
                        self.errors[-1]["recovery"] = f"re-planned {len(new)} new step(s)" if new else "no alternative found"
                    superseded = [s.title for s in steps[i + 1:]]
                    revised = steps[:i + 1] + new
                    for n, s in enumerate(revised):
                        s.idx = n
                    steps = await self.store.replace_steps(run.id, revised)
                    await self.bus.publish(run.id, "plan", {"steps": [s.to_dict() for s in steps], "replanned": True,
                                                            "superseded": superseded})
                    i += 1
                    if not new:
                        run.error = f"Step {step.idx + 1} failed and no alternative approach was found."
                        break

                # 4. Verify the goal against the real action log
                await self._status(run, "verifying")
                actions = await self.store.list_actions(run.id)
                try:
                    verification = await verify_goal(self.utility_llm, self.utility_model, run.goal, steps, actions)
                except ProviderError as e:
                    verification = {"achieved": "partial", "issues": [f"Verification unavailable: {e}"], "evidence": ""}
                await self.bus.publish(run.id, "verification", verification)

                # 5. One remediation pass if the goal isn't met and nothing hard-failed
                if verification["achieved"] != "yes" and not remediated and not run.error:
                    remediated = True
                    try:
                        extra = await self.planner.remediate(run.goal, catalog, steps, verification["issues"])
                    except ProviderError:
                        extra = []
                    if extra:
                        for n, s in enumerate(extra, len(steps)):
                            s.idx = n
                        steps = await self.store.replace_steps(run.id, steps + extra)
                        await self.bus.publish(run.id, "plan", {"steps": [s.to_dict() for s in steps], "remediation": True})
                        await self._status(run, "running")
                        continue
                break

            final = {"yes": "completed", "partial": "completed_with_issues"}.get(verification["achieved"], "failed")
            if run.error and final == "completed":
                final = "completed_with_issues"
            run.status = final
        except asyncio.CancelledError:
            run.status, run.error = "cancelled", run.error or "Cancelled by the user."
            for s in steps:
                if s.status in ("pending", "running"):
                    s.status = "skipped"
                    await self.store.update_step(run.id, s)
        except Exception as e:  # noqa: BLE001 — a run must always end with a report
            log.exception("run %s crashed", run.id)
            run.status, run.error = "failed", f"Internal error: {type(e).__name__}: {e}"

        # 6. Report (factual sections from data; short narrative from the model)
        run.verification = verification
        actions = await self.store.list_actions(run.id)
        approvals = await self.store.list_approvals(run.id)
        try:
            summary = await write_summary(self.utility_llm, self.utility_model, run, steps, actions, verification)
        except Exception:  # noqa: BLE001
            summary = ""
        run.report = build_report(run, steps, actions, approvals, verification, summary, self.errors)
        run.finished_at = now()
        await self.store.update_run(run)
        await self.bus.publish(run.id, "report", {"report": run.report})
        await self.bus.publish(run.id, "status", {"status": run.status, "final": True})
        return run

    async def _run_step(self, run: Run, plan: list[Step], step: Step, ctx: ToolContext) -> bool:
        step.status, step.started_at, step.error = "running", now(), ""
        await self._save_step(run, step, "step_started")
        prior: list[str] = []
        for attempt in range(1, self.step_retries + 2):
            step.attempts = attempt
            outcome = await self.executor.execute(run.id, run.goal, plan, step, ctx, prior)
            if outcome.status == "done":
                step.status, step.result, step.finished_at = "done", outcome.summary, now()
                await self._save_step(run, step, "step_done")
                return True
            err = outcome.error or outcome.summary or "Step failed without details."
            prior.append(err)
            self.errors.append({"step": step.idx, "attempt": attempt, "error": err})
            will_retry = attempt <= self.step_retries
            await self.bus.publish(run.id, "step_error", {"step": step.idx, "attempt": attempt, "error": err,
                                                          "will_retry": will_retry})
            if will_retry:
                self.errors[-1]["recovery"] = "retried with a different approach"
                await asyncio.sleep(min(self.backoff_base * 2 ** (attempt - 1), 8))  # back off (rate limits, flaky APIs)
        step.status, step.error, step.finished_at = "failed", prior[-1], now()
        await self._save_step(run, step, "step_failed")
        return False
