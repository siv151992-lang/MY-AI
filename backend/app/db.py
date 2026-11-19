"""Database access (asyncpg + pgvector). Every query that touches user data is scoped by user_id."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_conn)


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database not connected")
    return _pool


def vec(v: list[float] | None) -> str | None:
    """pgvector text literal; passed with a ::vector cast."""
    return None if v is None else "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def row(r: asyncpg.Record | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, uuid.UUID):
            d[k] = str(v)
        elif isinstance(v, list) and v and isinstance(v[0], uuid.UUID):
            d[k] = [str(x) for x in v]
    return d


def or_query(text: str) -> str:
    """Keyword fallback: match ANY term (ranked by ts_rank) instead of requiring all of them."""
    words = re.findall(r"\w{2,}", text.lower())[:32]
    return " or ".join(words)


def rows(rs: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [row(r) for r in rs]  # type: ignore[misc]


# ---------- users ----------
async def create_user(email: str, name: str, password_hash: str) -> dict:
    r = await pool().fetchrow(
        "INSERT INTO users(email, name, password_hash) VALUES ($1,$2,$3) RETURNING id, email, name, created_at",
        email, name, password_hash,
    )
    return row(r)  # type: ignore[return-value]


async def get_user_by_email(email: str) -> dict | None:
    return row(await pool().fetchrow("SELECT * FROM users WHERE email=$1", email))


async def get_user(user_id: str) -> dict | None:
    return row(await pool().fetchrow("SELECT id, email, name, created_at FROM users WHERE id=$1", user_id))


# ---------- conversations ----------
async def list_conversations(user_id: str, limit: int = 200) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, title, model_id, created_at, updated_at FROM conversations "
        "WHERE user_id=$1 AND workspace_id IS NULL ORDER BY updated_at DESC LIMIT $2", user_id, limit))


async def create_conversation(user_id: str, model_id: str | None) -> dict:
    return row(await pool().fetchrow(
        "INSERT INTO conversations(user_id, model_id) VALUES ($1,$2) RETURNING id, title, model_id, created_at, updated_at",
        user_id, model_id))  # type: ignore[return-value]


async def get_conversation(user_id: str, conv_id: str) -> dict | None:
    return row(await pool().fetchrow("SELECT * FROM conversations WHERE id=$1 AND user_id=$2", conv_id, user_id))


async def update_conversation(user_id: str, conv_id: str, **fields: Any) -> None:
    allowed = {"title", "model_id", "summary", "summarized_upto", "state"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    if not sets:
        return
    args += [conv_id, user_id]
    await pool().execute(
        f"UPDATE conversations SET {', '.join(sets)}, updated_at=now() WHERE id=${len(args)-1} AND user_id=${len(args)}",
        *args)


MIGRATIONS = [
    # 002 — reasoning engine: structured conversation state
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS state JSONB NOT NULL DEFAULT '{}'::jsonb",
    # 003 — autonomous agent runs
    """CREATE TABLE IF NOT EXISTS agent_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'planning',
  model_id     TEXT,
  report       TEXT NOT NULL DEFAULT '',
  verification JSONB,
  error        TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ
)""",
    """CREATE INDEX IF NOT EXISTS agent_runs_user ON agent_runs(user_id, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS agent_steps (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id           UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  idx              INTEGER NOT NULL,
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  tool             TEXT NOT NULL DEFAULT 'none',
  success_criteria TEXT NOT NULL DEFAULT '',
  status           TEXT NOT NULL DEFAULT 'pending',
  attempts         INTEGER NOT NULL DEFAULT 0,
  result           TEXT NOT NULL DEFAULT '',
  error            TEXT NOT NULL DEFAULT '',
  origin           TEXT NOT NULL DEFAULT 'plan',
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ
)""",
    """CREATE INDEX IF NOT EXISTS agent_steps_run ON agent_steps(run_id, idx)""",
    """CREATE TABLE IF NOT EXISTS agent_approvals (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_idx   INTEGER NOT NULL,
  tool       TEXT NOT NULL,
  args       JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary    TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
)""",
    """CREATE TABLE IF NOT EXISTS agent_actions (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_idx     INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  args_summary TEXT NOT NULL DEFAULT '',
  ok           BOOLEAN NOT NULL,
  output       TEXT NOT NULL DEFAULT '',
  approval     TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE INDEX IF NOT EXISTS agent_actions_run ON agent_actions(run_id, id)""",
    """CREATE TABLE IF NOT EXISTS agent_events (
  run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  type       TEXT NOT NULL,
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, seq)
)""",
]


VECTOR_ENABLED = True  # set by ensure_extensions(); False when pgvector isn't installed (native Windows setup)


async def ensure_extensions(c: asyncpg.Connection, disable_pgvector: bool = False) -> bool:
    """Enable pgvector when the server has it. Otherwise create a stand-in `vector` domain so the schema and
    `::vector` casts still work; embeddings are then switched off and search uses full-text only."""
    global VECTOR_ENABLED
    try:
        await c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    except asyncpg.PostgresError:
        pass  # only needed by old PostgreSQL versions; gen_random_uuid() is built in since 13
    has_vector = False
    if not disable_pgvector:
        try:
            await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
            has_vector = True
        except asyncpg.PostgresError:
            has_vector = False
    if not has_vector:
        exists = await c.fetchval("SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                                  "WHERE t.typname = 'vector' AND n.nspname = 'public'")
        if not exists:
            await c.execute("CREATE DOMAIN vector AS text")
        elif await c.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'"):
            has_vector = True  # extension already installed earlier (disable flag only affects new installs)
    VECTOR_ENABLED = has_vector
    return has_vector


def _for_mode(stmt: str, dim: int, vector: bool) -> str | None:
    if vector:
        return stmt.replace("{DIM}", str(int(dim)))
    if "vector_cosine_ops" in stmt:
        return None  # vector indexes need pgvector
    return stmt.replace("vector({DIM})", "vector")


async def migrate(embedding_dim: int = 1536, disable_pgvector: bool = False) -> None:
    from .documents.store import MIGRATIONS as DOCUMENT_MIGRATIONS  # 007 — document intelligence
    from .memory.store import MIGRATIONS as MEMORY_MIGRATIONS  # 004 — advanced memory
    from .research.store import MIGRATIONS as RESEARCH_MIGRATIONS  # 006 — deep research
    from .setup import MIGRATIONS as SETUP_MIGRATIONS  # 009 — settings entered in the app
    from .schema import BASE  # 001 — base tables (previously created by db/init/01-schema.sh)
    from .team.store import MIGRATIONS as TEAM_MIGRATIONS  # 008 — multi-agent orchestration
    from .workspace.store import MIGRATIONS as WORKSPACE_MIGRATIONS  # 005 — coding workspaces
    async with pool().acquire() as c:
        vector = await ensure_extensions(c, disable_pgvector)
        for stmt in (BASE + MIGRATIONS + MEMORY_MIGRATIONS + WORKSPACE_MIGRATIONS + RESEARCH_MIGRATIONS
                     + DOCUMENT_MIGRATIONS + TEAM_MIGRATIONS + SETUP_MIGRATIONS):
            sql = _for_mode(stmt, embedding_dim, vector)
            if sql:
                await c.execute(sql)


async def touch_conversation(conv_id: str) -> None:
    await pool().execute("UPDATE conversations SET updated_at=now() WHERE id=$1", conv_id)


async def delete_conversation(user_id: str, conv_id: str) -> bool:
    res = await pool().execute("DELETE FROM conversations WHERE id=$1 AND user_id=$2", conv_id, user_id)
    return res.endswith("1")


# ---------- messages ----------
async def list_messages(conv_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, seq, role, content, file_ids, meta, created_at FROM messages WHERE conversation_id=$1 ORDER BY seq",
        conv_id))


async def add_message(conv_id: str, role: str, content: str, file_ids: list[str] | None = None,
                      meta: dict | None = None) -> dict:
    async with pool().acquire() as c, c.transaction():
        seq = await c.fetchval("SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE conversation_id=$1", conv_id)
        r = await c.fetchrow(
            "INSERT INTO messages(conversation_id, seq, role, content, file_ids, meta) "
            "VALUES ($1,$2,$3,$4,$5::uuid[],$6) RETURNING id, seq, role, content, file_ids, meta, created_at",
            conv_id, seq, role, content, file_ids or [], meta or {})
    await touch_conversation(conv_id)
    return row(r)  # type: ignore[return-value]


async def delete_last_assistant(conv_id: str) -> None:
    await pool().execute(
        "DELETE FROM messages WHERE id = (SELECT id FROM messages WHERE conversation_id=$1 ORDER BY seq DESC LIMIT 1) "
        "AND role='assistant'", conv_id)


# ---------- files ----------
async def create_file(user_id: str, **f: Any) -> dict:
    return row(await pool().fetchrow(
        "INSERT INTO files(id, user_id, filename, mime_type, size_bytes, storage_path, kind, text_content, status) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id, filename, mime_type, size_bytes, kind, status, created_at",
        f["id"], user_id, f["filename"], f["mime_type"], f["size_bytes"], f["storage_path"], f["kind"],
        f.get("text_content"), f.get("status", "ready")))  # type: ignore[return-value]


async def list_files(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, filename, mime_type, size_bytes, kind, status, created_at FROM files WHERE user_id=$1 "
        "ORDER BY created_at DESC", user_id))


async def delete_file(user_id: str, file_id: str) -> dict | None:
    return row(await pool().fetchrow("DELETE FROM files WHERE id=$1 AND user_id=$2 RETURNING storage_path",
                                     file_id, user_id))


