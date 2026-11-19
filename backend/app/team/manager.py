"""Starts, tracks and cancels multi-agent runs (one asyncio task per run)."""
from __future__ import annotations

import asyncio
import logging

from ..autonomy.bus import EventBus
from . import store
from .orchestrator import Orchestrator

log = logging.getLogger("aria.team")


class TooManyRuns(Exception):
    pass


def default_pool():
    from ..agent.tools.code import make_code_tool
    from ..agent.tools.web import make_web_tools
    from ..config import get_settings
    from ..documents.tools import make_document_tools
    s = get_settings()
    tools = make_web_tools(s.tavily_api_key, s.brave_api_key) + make_document_tools()
    if s.sandbox_url:
        tools.append(make_code_tool(s.sandbox_url, s.sandbox_token))
    return {t.name: t for t in tools}


def default_models(model_id: str | None):
    from ..llm import registry
    spec, uspec = registry.get_model(model_id), registry.utility_model()
    reasoning = {"mode": spec.reasoning, "effort": "medium"}
    return (registry.provider_for(spec), spec.model), (registry.provider_for(uspec), uspec.model), reasoning


class TeamManager:
    def __init__(self, max_per_user: int = 2, max_parallel: int = 3):
        self.bus = EventBus(store.EventStore())
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, str] = {}
        self.max_per_user, self.max_parallel = max_per_user, max_parallel
        self.pool = default_pool          # injection points for tests
        self.models = default_models

    def is_active(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        return bool(t and not t.done())

    async def recover_after_restart(self) -> None:
        if n := await store.mark_interrupted():
            log.warning("marked %d unfinished team run(s) as failed after restart", n)

    async def start(self, user_id: str, goal: str, model_id: str | None) -> dict:
        self.models(model_id)  # raises RuntimeError when no model is configured
        if sum(1 for r, t in self.tasks.items() if self.owners.get(r) == user_id and not t.done()) >= self.max_per_user:
            raise TooManyRuns(f"You already have {self.max_per_user} team runs in progress.")
        run = await store.create_run(user_id, goal.strip(), model_id)
        await self.bus.publish(run["id"], "created", {"goal": run["goal"]})
        self.owners[run["id"]] = user_id
        self.tasks[run["id"]] = asyncio.create_task(self._run(user_id, run))
        return run

    async def _run(self, user_id: str, run: dict) -> None:
        rid = run["id"]

        async def publish(type_: str, data: dict) -> None:
            await self.bus.publish(rid, type_, data)
        try:
            (ml, mm), (ul, um), reasoning = self.models(run.get("model_id"))
            orch = Orchestrator(run=run, user_id=user_id, llm=ml, model=mm, utility_llm=ul, utility_model=um,
                                tool_pool=lambda: self.pool(), publish=publish, max_parallel=self.max_parallel, reasoning=reasoning)
            final, status = await orch.execute()
            await store.update_run(rid, status=status, final=final)
            await publish("final", {"status": status, "text": final[:200]})
            await publish("status", {"status": status, "final": True})
        except asyncio.CancelledError:
            await store.update_run(rid, status="cancelled", error="Cancelled by you.")
            await publish("status", {"status": "cancelled", "final": True})
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, RuntimeError) else f"Internal error ({type(e).__name__})."
            if not isinstance(e, RuntimeError):
                log.exception("team run %s crashed", rid)
            await store.update_run(rid, status="failed", error=msg)
            await publish("status", {"status": "failed", "error": msg, "final": True})
        finally:
            self.tasks.pop(rid, None)

    def cancel(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        if not t or t.done():
            return False
        t.cancel()
        return True


_manager: TeamManager | None = None


def get_manager() -> TeamManager:
    global _manager
    if _manager is None:
        _manager = TeamManager()
    return _manager
