"""RunManager: starts, tracks, cancels and approves autonomous runs (one asyncio task per run).

In-process by design (simple to deploy). For multi-replica deployments, move execution to a worker queue
(e.g. arq/Celery) and the bus to Redis pub/sub; the Store/EventBus interfaces are the seams for that.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from .bus import EventBus
from .models import TERMINAL, Run
from .permissions import PermissionGate
from .runner import AgentRunner

log = logging.getLogger("aria.autonomy")


class TooManyRuns(Exception):
    pass


class RunManager:
    def __init__(self, store, runner_factory: Callable[..., AgentRunner], *, approval_timeout_s: float = 1800,
                 max_concurrent_per_user: int = 3):
        self.store = store
        self.bus = EventBus(store)
        self.gate = PermissionGate(store, self.bus, approval_timeout_s)
        self.runner_factory = runner_factory
        self.max_concurrent = max_concurrent_per_user
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, str] = {}

    async def recover_after_restart(self) -> None:
        n = await self.store.mark_interrupted()
        if n:
            log.warning("marked %d unfinished agent run(s) as interrupted", n)

    def active_count(self, user_id: str) -> int:
        return sum(1 for rid, t in self.tasks.items() if self.owners.get(rid) == user_id and not t.done())

    async def start(self, user_id: str, goal: str, model_id: str | None) -> Run:
        if self.active_count(user_id) >= self.max_concurrent:
            raise TooManyRuns(f"You already have {self.max_concurrent} agent runs in progress.")
        run = await self.store.create_run(user_id, goal.strip(), model_id)
        await self.bus.publish(run.id, "created", {"goal": run.goal})
        runner = self.runner_factory(store=self.store, bus=self.bus, gate=self.gate, model_id=model_id)
        from ..hub import usage
        with usage.scope(user_id, "agent"):
            task = asyncio.create_task(runner.run(run), name=f"agent-run-{run.id}")
        self.tasks[run.id], self.owners[run.id] = task, user_id
        task.add_done_callback(lambda t, rid=run.id: self.tasks.pop(rid, None))
        return run

    async def cancel(self, user_id: str, run_id: str) -> bool:
        if self.owners.get(run_id) != user_id or run_id not in self.tasks:
            return False
        self.gate.cancel_run(run_id)
        self.tasks[run_id].cancel()
        return True

    async def decide(self, user_id: str, run_id: str, approval_id: str, approved: bool) -> bool:
        if self.owners.get(run_id) != user_id:
            return False
        return await self.gate.decide(run_id, approval_id, approved)

    def is_active(self, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        return bool(t and not t.done())

    async def wait(self, run_id: str) -> None:
        t = self.tasks.get(run_id)
        if t:
            await asyncio.gather(t, return_exceptions=True)


# ------------------------------------------------------------------ production wiring
_manager: RunManager | None = None


def build_agent_tools():
    """Chat tools + action tools that are configured on this server."""
    from .. import db
    from ..agent.tools.actions import make_create_file_tool, make_email_tool, make_http_tool, make_slack_tool
    from ..config import get_settings
    from ..services.chat import build_tools

    s = get_settings()
    reg = build_tools()

    async def save_record(user_id, file_id, filename, mime, size, path, text):
        await db.create_file(user_id, id=file_id, filename=filename, mime_type=mime, size_bytes=size,
                             storage_path=path, kind="document", text_content=text)

    reg.add(make_create_file_tool(s.upload_dir, save_record))
    if s.enable_http_tool:
        reg.add(make_http_tool())
    if s.smtp_host and s.smtp_from:
        reg.add(make_email_tool(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from))
    if s.slack_webhook_url:
        reg.add(make_slack_tool(s.slack_webhook_url))
    return reg


def get_manager() -> RunManager:
    global _manager
    if _manager is None:
        from .. import db
        from ..config import get_settings
        from ..llm import registry
        from .store import PgStore

        s = get_settings()

        def factory(*, store, bus, gate, model_id):
            spec = registry.get_model(model_id)
            uspec = registry.utility_model()
            return AgentRunner(store=store, bus=bus, gate=gate, llm=registry.provider_for(spec), model=spec.model,
                               utility_llm=registry.provider_for(uspec), utility_model=uspec.model,
                               tools=build_agent_tools(), reasoning={"mode": spec.reasoning, "effort": "medium"},
                               max_plan_steps=s.autonomy_max_plan_steps, step_retries=s.autonomy_step_retries,
                               max_replans=s.autonomy_max_replans, calls_per_step=s.autonomy_calls_per_step,
                               max_tokens=spec.max_tokens)

        _manager = RunManager(PgStore(db.pool), factory, approval_timeout_s=s.autonomy_approval_timeout_min * 60,
                              max_concurrent_per_user=s.autonomy_max_concurrent_runs)
    return _manager
