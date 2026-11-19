"""Persistence for runs, steps, approvals and events. MemoryStore (tests/dev) and PgStore (production)."""
from __future__ import annotations

import uuid
from typing import Protocol

from .models import ActionRecord, Approval, Run, Step, now


class Store(Protocol):
    async def create_run(self, user_id: str, goal: str, model_id: str | None) -> Run: ...
    async def get_run(self, user_id: str, run_id: str) -> Run | None: ...
    async def list_runs(self, user_id: str) -> list[Run]: ...
    async def update_run(self, run: Run) -> None: ...
    async def replace_steps(self, run_id: str, steps: list[Step]) -> list[Step]: ...
    async def update_step(self, run_id: str, step: Step) -> None: ...
    async def list_steps(self, run_id: str) -> list[Step]: ...
    async def add_approval(self, a: Approval) -> None: ...
    async def update_approval(self, a: Approval) -> None: ...
    async def list_approvals(self, run_id: str) -> list[Approval]: ...
    async def add_action(self, run_id: str, a: ActionRecord) -> None: ...
    async def list_actions(self, run_id: str) -> list[ActionRecord]: ...
    async def add_event(self, run_id: str, type_: str, data: dict) -> dict: ...
    async def list_events(self, run_id: str, after: int = 0) -> list[dict]: ...
    async def mark_interrupted(self) -> int: ...


class MemoryStore:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.steps: dict[str, list[Step]] = {}
        self.approvals: dict[str, list[Approval]] = {}
        self.actions: dict[str, list[ActionRecord]] = {}
        self.events: dict[str, list[dict]] = {}

    async def create_run(self, user_id, goal, model_id):
        r = Run(id=str(uuid.uuid4()), user_id=user_id, goal=goal, model_id=model_id)
        self.runs[r.id] = r
        return r

    async def get_run(self, user_id, run_id):
        r = self.runs.get(run_id)
        return r if r and r.user_id == user_id else None

    async def list_runs(self, user_id):
        return sorted((r for r in self.runs.values() if r.user_id == user_id), key=lambda r: r.created_at, reverse=True)

    async def update_run(self, run):
        run.updated_at = now()
        self.runs[run.id] = run

    async def replace_steps(self, run_id, steps):
        for s in steps:
            s.id = s.id or str(uuid.uuid4())
        self.steps[run_id] = list(steps)
        return self.steps[run_id]

    async def update_step(self, run_id, step):
        pass  # steps are shared objects in memory

    async def list_steps(self, run_id):
        return list(self.steps.get(run_id, []))

    async def add_approval(self, a):
        self.approvals.setdefault(a.run_id, []).append(a)

    async def update_approval(self, a):
        pass

    async def list_approvals(self, run_id):
        return list(self.approvals.get(run_id, []))

    async def add_action(self, run_id, a):
        self.actions.setdefault(run_id, []).append(a)

    async def list_actions(self, run_id):
        return list(self.actions.get(run_id, []))

    async def add_event(self, run_id, type_, data):
        evs = self.events.setdefault(run_id, [])
        ev = {"seq": len(evs) + 1, "type": type_, "data": data, "at": now()}
        evs.append(ev)
        return ev

    async def list_events(self, run_id, after=0):
        return [e for e in self.events.get(run_id, []) if e["seq"] > after]

    async def mark_interrupted(self):
        n = 0
        for r in self.runs.values():
            if r.status not in ("completed", "completed_with_issues", "failed", "cancelled"):
                r.status, r.error, n = "failed", "Interrupted by a server restart.", n + 1
        return n


class PgStore:
    """Postgres implementation (asyncpg pool from app.db)."""

    def __init__(self, pool_getter):
        self._pool = pool_getter

    @staticmethod
    def _run(r) -> Run:
        return Run(id=str(r["id"]), user_id=str(r["user_id"]), goal=r["goal"], status=r["status"],
                   model_id=r["model_id"], report=r["report"], verification=r["verification"], error=r["error"],
                   created_at=r["created_at"].isoformat(), updated_at=r["updated_at"].isoformat(),
                   finished_at=r["finished_at"].isoformat() if r["finished_at"] else None)

    @staticmethod
    def _step(r) -> Step:
        return Step(id=str(r["id"]), idx=r["idx"], title=r["title"], description=r["description"], tool=r["tool"],
                    success_criteria=r["success_criteria"], status=r["status"], attempts=r["attempts"],
                    result=r["result"], error=r["error"], origin=r["origin"],
                    started_at=r["started_at"].isoformat() if r["started_at"] else None,
                    finished_at=r["finished_at"].isoformat() if r["finished_at"] else None)

    async def create_run(self, user_id, goal, model_id):
        r = await self._pool().fetchrow(
            "INSERT INTO agent_runs(user_id, goal, model_id) VALUES ($1,$2,$3) RETURNING *", user_id, goal, model_id)
        return self._run(r)

    async def get_run(self, user_id, run_id):
        try:
            r = await self._pool().fetchrow("SELECT * FROM agent_runs WHERE id=$1 AND user_id=$2", run_id, user_id)
        except Exception:
            return None
        return self._run(r) if r else None

    async def list_runs(self, user_id):
        return [self._run(r) for r in await self._pool().fetch(
            "SELECT * FROM agent_runs WHERE user_id=$1 ORDER BY created_at DESC LIMIT 100", user_id)]

    async def update_run(self, run):
        await self._pool().execute(
            "UPDATE agent_runs SET status=$2, report=$3, verification=$4, error=$5, updated_at=now(), "
            "finished_at=CASE WHEN $2 IN ('completed','completed_with_issues','failed','cancelled') "
            "THEN COALESCE(finished_at, now()) ELSE NULL END WHERE id=$1",
            run.id, run.status, run.report, run.verification, run.error)

    async def replace_steps(self, run_id, steps):
        async with self._pool().acquire() as c, c.transaction():
            await c.execute("DELETE FROM agent_steps WHERE run_id=$1", run_id)
            out = []
            for s in steps:
                r = await c.fetchrow(
                    "INSERT INTO agent_steps(run_id, idx, title, description, tool, success_criteria, status, attempts,"
                    " result, error, origin, started_at, finished_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
                    " $12::timestamptz,$13::timestamptz) RETURNING *",
                    run_id, s.idx, s.title, s.description, s.tool, s.success_criteria, s.status, s.attempts, s.result,
                    s.error, s.origin, _ts(s.started_at), _ts(s.finished_at))
                s.id = str(r["id"])
                out.append(s)
            return out

    async def update_step(self, run_id, step):
        await self._pool().execute(
            "UPDATE agent_steps SET status=$2, attempts=$3, result=$4, error=$5, started_at=$6::timestamptz, "
            "finished_at=$7::timestamptz WHERE id=$1",
            step.id, step.status, step.attempts, step.result[:20000], step.error[:4000], _ts(step.started_at),
            _ts(step.finished_at))

    async def list_steps(self, run_id):
        return [self._step(r) for r in await self._pool().fetch(
            "SELECT * FROM agent_steps WHERE run_id=$1 ORDER BY idx", run_id)]

    async def add_approval(self, a):
        r = await self._pool().fetchrow(
            "INSERT INTO agent_approvals(run_id, step_idx, tool, args, summary) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            a.run_id, a.step_idx, a.tool, a.args, a.summary)
        a.id = str(r["id"])

    async def update_approval(self, a):
        await self._pool().execute("UPDATE agent_approvals SET status=$2, decided_at=now() WHERE id=$1", a.id, a.status)

    async def list_approvals(self, run_id):
        return [Approval(id=str(r["id"]), run_id=str(r["run_id"]), step_idx=r["step_idx"], tool=r["tool"],
                         args=r["args"], summary=r["summary"], status=r["status"],
                         created_at=r["created_at"].isoformat(),
                         decided_at=r["decided_at"].isoformat() if r["decided_at"] else None)
                for r in await self._pool().fetch("SELECT * FROM agent_approvals WHERE run_id=$1 ORDER BY created_at", run_id)]

    async def add_action(self, run_id, a):
        await self._pool().execute(
            "INSERT INTO agent_actions(run_id, step_idx, tool, args_summary, ok, output, approval) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)", run_id, a.step_idx, a.tool, a.args_summary[:1000], a.ok, a.output[:4000],
            a.approval)

    async def list_actions(self, run_id):
        return [ActionRecord(step_idx=r["step_idx"], tool=r["tool"], args_summary=r["args_summary"], ok=r["ok"],
                             output=r["output"], approval=r["approval"])
                for r in await self._pool().fetch("SELECT * FROM agent_actions WHERE run_id=$1 ORDER BY id", run_id)]

    async def add_event(self, run_id, type_, data):
        r = await self._pool().fetchrow(
            "INSERT INTO agent_events(run_id, seq, type, data) VALUES "
            "($1, (SELECT COALESCE(MAX(seq),0)+1 FROM agent_events WHERE run_id=$1), $2, $3) RETURNING seq, created_at",
            run_id, type_, data)
        return {"seq": r["seq"], "type": type_, "data": data, "at": r["created_at"].isoformat()}

    async def list_events(self, run_id, after=0):
        return [{"seq": r["seq"], "type": r["type"], "data": r["data"], "at": r["created_at"].isoformat()}
                for r in await self._pool().fetch(
                    "SELECT seq, type, data, created_at FROM agent_events WHERE run_id=$1 AND seq>$2 ORDER BY seq",
                    run_id, after)]

    async def mark_interrupted(self):
        res = await self._pool().execute(
            "UPDATE agent_runs SET status='failed', error='Interrupted by a server restart.', finished_at=now() "
            "WHERE status NOT IN ('completed','completed_with_issues','failed','cancelled')")
        await self._pool().execute("UPDATE agent_approvals SET status='expired' WHERE status='pending'")
        return int(res.split()[-1])


def _ts(v):
    if v is None:
        return None
    from datetime import datetime
    return datetime.fromisoformat(v)
