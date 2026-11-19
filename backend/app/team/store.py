"""PostgreSQL persistence for multi-agent runs, their tasks and progress events."""
from __future__ import annotations

import asyncio
from typing import Any

from ..db import pool, row, rows

MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS team_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'planning',
  model_id     TEXT,
  plan_notes   JSONB NOT NULL DEFAULT '[]'::jsonb,
  conflicts    JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification JSONB,
  final        TEXT NOT NULL DEFAULT '',
  error        TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ
)""",
    "CREATE INDEX IF NOT EXISTS team_runs_user ON team_runs(user_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS team_tasks (
  run_id      UUID NOT NULL REFERENCES team_runs(id) ON DELETE CASCADE,
  id          TEXT NOT NULL,
  agent       TEXT NOT NULL,
  title       TEXT NOT NULL,
  instruction TEXT NOT NULL,
  depends_on  TEXT[] NOT NULL DEFAULT '{}',
  expected_output TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  result      TEXT NOT NULL DEFAULT '',
  sources     TEXT[] NOT NULL DEFAULT '{}',
  confidence  TEXT NOT NULL DEFAULT '',
  notes       TEXT NOT NULL DEFAULT '',
  error       TEXT NOT NULL DEFAULT '',
  tool_calls  INTEGER NOT NULL DEFAULT 0,
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  PRIMARY KEY (run_id, id)
)""",
    """CREATE TABLE IF NOT EXISTS team_events (
  run_id     UUID NOT NULL REFERENCES team_runs(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  type       TEXT NOT NULL,
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, seq)
)""",
]
TERMINAL = {"completed", "completed_with_issues", "failed", "cancelled"}
RUN_COLS = "id, goal, status, model_id, plan_notes, conflicts, verification, final, error, created_at, updated_at, finished_at"


async def create_run(user_id: str, goal: str, model_id: str | None) -> dict:
    return row(await pool().fetchrow(f"INSERT INTO team_runs(user_id, goal, model_id) VALUES ($1,$2,$3) RETURNING {RUN_COLS}",
                                     user_id, goal, model_id))  # type: ignore[return-value]


async def get_run(user_id: str, run_id: str) -> dict | None:
    try:
        return row(await pool().fetchrow(f"SELECT {RUN_COLS} FROM team_runs WHERE id=$1 AND user_id=$2", run_id, user_id))
    except Exception:
        return None


async def list_runs(user_id: str) -> list[dict]:
    return rows(await pool().fetch("SELECT id, goal, status, created_at, finished_at FROM team_runs WHERE user_id=$1 "
                                   "ORDER BY created_at DESC LIMIT 200", user_id))


async def update_run(run_id: str, **f: Any) -> None:
    sets, args = [], []
    for k, v in f.items():
        if k in ("status", "plan_notes", "conflicts", "verification", "final", "error"):
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    if f.get("status") in TERMINAL:
        sets.append("finished_at=now()")
    args.append(run_id)
    await pool().execute(f"UPDATE team_runs SET {', '.join(sets)}, updated_at=now() WHERE id=${len(args)}", *args)


async def delete_run(user_id: str, run_id: str) -> bool:
    return (await pool().execute("DELETE FROM team_runs WHERE id=$1 AND user_id=$2", run_id, user_id)).endswith(" 1")


async def mark_interrupted() -> int:
    res = await pool().execute("UPDATE team_runs SET status='failed', error='Interrupted by a server restart.', finished_at=now() "
                               "WHERE status NOT IN ('completed','completed_with_issues','failed','cancelled')")
    return int(res.split()[-1])


async def save_tasks(run_id: str, tasks: list) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.execute("DELETE FROM team_tasks WHERE run_id=$1", run_id)
        await c.executemany(
            "INSERT INTO team_tasks(run_id, id, agent, title, instruction, depends_on, expected_output) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            [(run_id, t.id, t.agent, t.title, t.instruction, t.depends_on, t.expected_output) for t in tasks])


async def update_task(run_id: str, t) -> None:
    await pool().execute(
        "UPDATE team_tasks SET status=$3, attempts=$4, result=$5, sources=$6, confidence=$7, notes=$8, error=$9, tool_calls=$10, "
        "started_at=COALESCE(started_at, CASE WHEN $3='running' THEN now() END), "
        "finished_at=CASE WHEN $3 IN ('done','failed','skipped') THEN now() ELSE finished_at END WHERE run_id=$1 AND id=$2",
        run_id, t.id, t.status, t.attempts, t.result, t.sources, t.confidence, t.notes, t.error, t.tool_calls)


async def tasks(run_id: str) -> list[dict]:
    rs = rows(await pool().fetch("SELECT * FROM team_tasks WHERE run_id=$1", run_id))
    for r in rs:
        r.pop("run_id", None)
    return sorted(rs, key=lambda r: int(r["id"][1:]))


class EventStore:
    _locks: dict[str, asyncio.Lock] = {}

    async def add_event(self, run_id: str, type_: str, data: dict) -> dict:
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            r = await pool().fetchrow(
                "INSERT INTO team_events(run_id, seq, type, data) VALUES ($1, (SELECT COALESCE(MAX(seq),0)+1 FROM team_events "
                "WHERE run_id=$1), $2, $3) RETURNING seq, type, data, created_at", run_id, type_, data)
        if data.get("final"):
            self._locks.pop(run_id, None)
        return {"seq": r["seq"], "type": r["type"], "data": r["data"], "at": r["created_at"].isoformat()}

    async def list_events(self, run_id: str, after: int = 0) -> list[dict]:
        rs = await pool().fetch("SELECT seq, type, data, created_at FROM team_events WHERE run_id=$1 AND seq>$2 ORDER BY seq",
                                run_id, after)
        return [{"seq": r["seq"], "type": r["type"], "data": r["data"], "at": r["created_at"].isoformat()} for r in rs]
