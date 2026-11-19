"""PostgreSQL persistence for workspaces and their activity log. Every query is scoped by user_id."""
from __future__ import annotations

from typing import Any

from ..db import pool, row, rows

MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS workspaces (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name           TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  source         TEXT NOT NULL DEFAULT 'blank',   -- blank | clone | import
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS workspaces_user ON workspaces(user_id, updated_at DESC)",
    """CREATE TABLE IF NOT EXISTS workspace_activity (
  id           BIGSERIAL PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  actor        TEXT NOT NULL,            -- user | assistant
  kind         TEXT NOT NULL,            -- write | edit | delete | move | command | commit | push | discard | restore | approval
  summary      TEXT NOT NULL,
  detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS workspace_activity_ws ON workspace_activity(workspace_id, id DESC)",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS conversations_workspace ON conversations(workspace_id, updated_at DESC)",
]

COLS = "id, name, description, source, created_at, updated_at"


async def create(user_id: str, name: str, description: str, source: str) -> dict:
    return row(await pool().fetchrow(
        f"INSERT INTO workspaces(user_id, name, description, source) VALUES ($1,$2,$3,$4) RETURNING {COLS}",
        user_id, name, description, source))  # type: ignore[return-value]


async def get(user_id: str, ws_id: str) -> dict | None:
    try:
        return row(await pool().fetchrow(f"SELECT {COLS} FROM workspaces WHERE id=$1 AND user_id=$2", ws_id, user_id))
    except Exception:  # malformed UUID
        return None


async def list_(user_id: str) -> list[dict]:
    return rows(await pool().fetch(f"SELECT {COLS} FROM workspaces WHERE user_id=$1 ORDER BY updated_at DESC", user_id))


async def update(user_id: str, ws_id: str, **fields: Any) -> dict | None:
    sets, args = [], []
    for k, v in fields.items():
        if k in ("name", "description"):
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    args += [ws_id, user_id]
    return row(await pool().fetchrow(
        f"UPDATE workspaces SET {', '.join(sets + ['updated_at=now()'])} WHERE id=${len(args) - 1} AND user_id=${len(args)} "
        f"RETURNING {COLS}", *args))


async def touch(ws_id: str) -> None:
    await pool().execute("UPDATE workspaces SET updated_at=now() WHERE id=$1", ws_id)


async def delete(user_id: str, ws_id: str) -> bool:
    return (await pool().execute("DELETE FROM workspaces WHERE id=$1 AND user_id=$2", ws_id, user_id)).endswith(" 1")


async def log(ws_id: str, user_id: str, actor: str, kind: str, summary: str, **detail: Any) -> None:
    await pool().execute(
        "INSERT INTO workspace_activity(workspace_id, user_id, actor, kind, summary, detail) VALUES ($1,$2,$3,$4,$5,$6)",
        ws_id, user_id, actor, kind, summary[:500], detail)


async def activity(user_id: str, ws_id: str, limit: int = 200) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, actor, kind, summary, detail, created_at FROM workspace_activity "
        f"WHERE workspace_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT {int(limit)}", ws_id, user_id))


async def conversations(user_id: str, ws_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, title, model_id, created_at, updated_at FROM conversations "
        "WHERE user_id=$1 AND workspace_id=$2 ORDER BY updated_at DESC", user_id, ws_id))


async def create_conversation(user_id: str, ws_id: str, model_id: str | None) -> dict:
    return row(await pool().fetchrow(
        "INSERT INTO conversations(user_id, model_id, workspace_id) VALUES ($1,$2,$3) "
        "RETURNING id, title, model_id, created_at, updated_at", user_id, model_id, ws_id))  # type: ignore[return-value]
