"""PostgreSQL + pgvector persistence for the memory system. Every query is scoped by user_id."""
from __future__ import annotations

import contextvars
from typing import Any

from .. import db
from ..db import or_query, pool, row, rows, vec

COLS = ("id, content, category, status, source, importance, confidence, sensitivity, pinned, access_count, "
        "created_at, updated_at, last_accessed_at, expires_at, source_conversation_id, source_excerpt, "
        "supersedes_id, version, project_id")

# Context-aware memory: while a chat turn runs, PROJECT_SCOPE holds the conversation's project id (or None outside
# projects). Recall then sees global memories plus that project's memories only, and new memories are tagged with the
# project. When unset (memory management API), nothing is filtered.
_UNSET = object()
PROJECT_SCOPE: contextvars.ContextVar = contextvars.ContextVar("memory_project_scope", default=_UNSET)


def _scope(n: int) -> tuple[str, list]:
    pid = PROJECT_SCOPE.get()
    if pid is _UNSET:
        return "", []
    if pid is None:
        return " AND project_id IS NULL", []
    return f" AND (project_id IS NULL OR project_id = ${n}::uuid)", [str(pid)]
STATUSES = ("pending", "active", "archived")
EDITABLE = {"content", "category", "importance", "pinned", "status", "sensitivity", "confidence", "expires_at"}

# Migration 004: advanced memory. {DIM} is replaced with EMBEDDING_DIM by db.migrate().
MIGRATIONS = [
    """ALTER TABLE memories
         ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'fact',
         ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
         ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual',
         ADD COLUMN IF NOT EXISTS importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
         ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
         ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal',
         ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false,
         ADD COLUMN IF NOT EXISTS access_count INTEGER NOT NULL DEFAULT 0,
         ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
         ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
         ADD COLUMN IF NOT EXISTS source_excerpt TEXT,
         ADD COLUMN IF NOT EXISTS supersedes_id UUID REFERENCES memories(id) ON DELETE SET NULL,
         ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1""",
    "CREATE INDEX IF NOT EXISTS memories_user_status ON memories(user_id, status, importance DESC)",
    """CREATE TABLE IF NOT EXISTS memory_revisions (
  id          BIGSERIAL PRIMARY KEY,
  memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version     INTEGER NOT NULL,
  content     TEXT NOT NULL,
  category    TEXT NOT NULL,
  importance  DOUBLE PRECISION NOT NULL,
  status      TEXT NOT NULL,
  changed_by  TEXT NOT NULL,            -- user | assistant | system
  reason      TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS memory_revisions_mem ON memory_revisions(memory_id, version)",
    """CREATE TABLE IF NOT EXISTS memory_settings (
  user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  settings   JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    # memory_id deliberately has no FK: the audit trail outlives deleted memories but never stores their content.
    """CREATE TABLE IF NOT EXISTS memory_audit (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  memory_id  UUID,
  action     TEXT NOT NULL,
  detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS memory_audit_user ON memory_audit(user_id, id DESC)",
    """CREATE TABLE IF NOT EXISTS conversation_segments (
  id              BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_seq        INTEGER NOT NULL,
  to_seq          INTEGER NOT NULL,
  summary         TEXT NOT NULL,
  embedding       vector({DIM}),
  tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS conversation_segments_conv ON conversation_segments(conversation_id, from_seq)",
    "CREATE INDEX IF NOT EXISTS conversation_segments_tsv ON conversation_segments USING gin(tsv)",
    "CREATE INDEX IF NOT EXISTS conversation_segments_embedding ON conversation_segments "
    "USING hnsw (embedding vector_cosine_ops)",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT true",
]


# ---------- memories ----------
async def insert(user_id: str, *, content: str, category: str, status: str, source: str, importance: float,
                 confidence: float, sensitivity: str, pinned: bool, embedding: list[float] | None,
                 conv_id: str | None, excerpt: str | None, supersedes_id: str | None, changed_by: str,
                 reason: str = "") -> dict:
    async with pool().acquire() as c, c.transaction():
        r = await c.fetchrow(
            f"INSERT INTO memories(user_id, content, category, status, source, importance, confidence, sensitivity, "
            f"pinned, embedding, source_conversation_id, source_excerpt, supersedes_id, project_id) "
            f"VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::vector,$11,$12,$13,$14::uuid) RETURNING {COLS}",
            user_id, content, category, status, source, importance, confidence, sensitivity, pinned, vec(embedding),
            conv_id, excerpt, supersedes_id, _project_for_insert())
        await _revision(c, user_id, r, changed_by, reason)
    return row(r)  # type: ignore[return-value]


async def _revision(c, user_id: str, r, changed_by: str, reason: str) -> None:
    await c.execute(
        "INSERT INTO memory_revisions(memory_id, user_id, version, content, category, importance, status, changed_by, "
        "reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        r["id"], user_id, r["version"], r["content"], r["category"], r["importance"], r["status"], changed_by, reason)


async def update(user_id: str, mem_id: str, fields: dict[str, Any], *, embedding: list[float] | None = None,
                 clear_embedding: bool = False, changed_by: str = "user", reason: str = "") -> dict | None:
    sets, args = [], []
    for k, v in fields.items():
        if k in EDITABLE:
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    if embedding is not None:
        args.append(vec(embedding))
        sets.append(f"embedding=${len(args)}::vector")
    elif clear_embedding:
        sets.append("embedding=NULL")
    if not sets:
        return await get(user_id, mem_id)
    args += [mem_id, user_id]
    async with pool().acquire() as c, c.transaction():
        r = await c.fetchrow(
            f"UPDATE memories SET {', '.join(sets)}, version=version+1, updated_at=now() "
            f"WHERE id=${len(args) - 1} AND user_id=${len(args)} RETURNING {COLS}", *args)
        if r:
            await _revision(c, user_id, r, changed_by, reason)
    return row(r)


async def get(user_id: str, mem_id: str) -> dict | None:
    try:
        return row(await pool().fetchrow(f"SELECT {COLS} FROM memories WHERE id=$1 AND user_id=$2", mem_id, user_id))
    except Exception:  # malformed UUID
        return None


async def list_(user_id: str, *, statuses: tuple[str, ...] = STATUSES, category: str | None = None,
                sort: str = "recent", limit: int = 500) -> list[dict]:
    order = {"importance": "pinned DESC, importance DESC, updated_at DESC",
             "used": "access_count DESC, last_accessed_at DESC NULLS LAST",
             "recent": "pinned DESC, updated_at DESC"}.get(sort, "updated_at DESC")
    args: list[Any] = [user_id, list(statuses)]
    cat = ""
    if category:
        args.append(category)
        cat = f"AND category=${len(args)}"
    return rows(await pool().fetch(
        f"SELECT {COLS} FROM memories WHERE user_id=$1 AND status = ANY($2::text[]) {cat} "
        f"ORDER BY {order} LIMIT {int(limit)}", *args))


async def by_conversation(user_id: str, conv_id: str) -> list[dict]:
    return rows(await pool().fetch(
        f"SELECT {COLS} FROM memories WHERE user_id=$1 AND source_conversation_id=$2 ORDER BY created_at",
        user_id, conv_id))


async def missing_embeddings(limit: int = 100) -> list[dict]:
    return rows(await pool().fetch(
        f"SELECT id, user_id, content FROM memories WHERE embedding IS NULL LIMIT {int(limit)}"))


async def set_embedding(mem_id: str, embedding: list[float]) -> None:
    await pool().execute("UPDATE memories SET embedding=$2::vector WHERE id=$1", mem_id, vec(embedding))


async def counts(user_id: str) -> dict[str, int]:
    rs = await pool().fetch("SELECT status, count(*) AS n FROM memories WHERE user_id=$1 GROUP BY status", user_id)
    out = {s: 0 for s in STATUSES}
    out.update({r["status"]: r["n"] for r in rs})
    return out


def _project_for_insert() -> str | None:
    pid = PROJECT_SCOPE.get()
    return None if pid is _UNSET or pid is None else str(pid)


async def pinned(user_id: str, limit: int = 12) -> list[dict]:
    sc, args = _scope(2)
    return rows(await pool().fetch(
        f"SELECT {COLS} FROM memories WHERE user_id=$1 AND status='active' AND pinned{sc} "
        f"ORDER BY importance DESC LIMIT {int(limit)}", user_id, *args))


async def vector_search(user_id: str, embedding: list[float], statuses: tuple[str, ...], k: int) -> list[dict]:
    async with pool().acquire() as c, c.transaction():
        await c.execute("SET LOCAL hnsw.ef_search = 200")  # per-user filtering happens after the ANN scan
        return rows(await c.fetch(
            f"SELECT {COLS}, 1 - (embedding <=> $2::vector) AS sim FROM memories "
            f"WHERE user_id=$1 AND status = ANY($3::text[]) AND embedding IS NOT NULL{_scope(4)[0]} "
            f"ORDER BY embedding <=> $2::vector LIMIT {int(k)}", user_id, vec(embedding), list(statuses), *_scope(4)[1]))


async def keyword_search(user_id: str, query: str, statuses: tuple[str, ...], k: int) -> list[dict]:
    q = or_query(query)
    if not q:
        return []
    return rows(await pool().fetch(
        f"SELECT {COLS}, ts_rank(tsv, q) AS kw FROM memories, websearch_to_tsquery('english', $2) q "
        f"WHERE user_id=$1 AND status = ANY($3::text[]) AND tsv @@ q{_scope(4)[0]} ORDER BY kw DESC LIMIT {int(k)}",
        user_id, q, list(statuses), *_scope(4)[1]))


async def touch(user_id: str, ids: list[str]) -> None:
    if ids:
        await pool().execute(
            "UPDATE memories SET access_count=access_count+1, last_accessed_at=now() "
            "WHERE user_id=$1 AND id = ANY($2::uuid[])", user_id, ids)


async def delete(user_id: str, mem_id: str) -> bool:
    try:
        res = await pool().execute("DELETE FROM memories WHERE id=$1 AND user_id=$2", mem_id, user_id)
    except Exception:
        return False
    return res.endswith(" 1")


async def delete_where(user_id: str, statuses: tuple[str, ...]) -> int:
    res = await pool().execute("DELETE FROM memories WHERE user_id=$1 AND status = ANY($2::text[])",
                               user_id, list(statuses))
    return int(res.split()[-1])


async def delete_ids(user_id: str, ids: list[str]) -> int:
    res = await pool().execute("DELETE FROM memories WHERE user_id=$1 AND id = ANY($2::uuid[])", user_id, ids)
    return int(res.split()[-1])


async def revisions(user_id: str, mem_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT version, content, category, importance, status, changed_by, reason, created_at FROM memory_revisions "
        "WHERE memory_id=$1 AND user_id=$2 ORDER BY version", mem_id, user_id))


async def all_revisions(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT memory_id, version, content, category, importance, status, changed_by, reason, created_at "
        "FROM memory_revisions WHERE user_id=$1 ORDER BY memory_id, version", user_id))


async def expire(user_id: str | None, pending_days: dict[str, int], retention: dict[str, int]) -> list[dict]:
    """Delete stale pending suggestions and memories past the user's retention window. Returns what was removed."""
    removed: list[dict] = []
    async with pool().acquire() as c, c.transaction():
        removed += rows(await c.fetch(
            "DELETE FROM memories WHERE (expires_at IS NOT NULL AND expires_at < now()) "
            + ("AND user_id=$1 " if user_id else "") + "RETURNING id, user_id, status",
            *([user_id] if user_id else [])))
        for uid, days in pending_days.items():
            removed += rows(await c.fetch(
                "DELETE FROM memories WHERE user_id=$1 AND status='pending' AND created_at < now() - make_interval(days => $2) "
                "RETURNING id, user_id, status", uid, days))
        for uid, days in retention.items():
            removed += rows(await c.fetch(
                "DELETE FROM memories WHERE user_id=$1 AND status <> 'pending' AND NOT pinned "
                "AND COALESCE(last_accessed_at, updated_at) < now() - make_interval(days => $2) "
                "RETURNING id, user_id, status", uid, days))
    return removed


# ---------- settings ----------
async def get_settings(user_id: str) -> dict:
    return await pool().fetchval("SELECT settings FROM memory_settings WHERE user_id=$1", user_id) or {}


async def put_settings(user_id: str, settings: dict) -> None:
    await pool().execute(
        "INSERT INTO memory_settings(user_id, settings) VALUES ($1,$2) "
        "ON CONFLICT (user_id) DO UPDATE SET settings=EXCLUDED.settings, updated_at=now()", user_id, settings)


async def all_settings() -> dict[str, dict]:
    return {str(r["user_id"]): r["settings"] for r in await pool().fetch("SELECT user_id, settings FROM memory_settings")}


async def users_with_pending() -> list[str]:
    return [str(r["user_id"]) for r in await pool().fetch("SELECT DISTINCT user_id FROM memories WHERE status='pending'")]


# ---------- audit ----------
async def audit(user_id: str, action: str, memory_id: str | None = None, **detail: Any) -> None:
    await pool().execute("INSERT INTO memory_audit(user_id, memory_id, action, detail) VALUES ($1,$2,$3,$4)",
                         user_id, memory_id, action, detail)


async def audit_log(user_id: str, limit: int = 200) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, memory_id, action, detail, created_at FROM memory_audit WHERE user_id=$1 "
        f"ORDER BY id DESC LIMIT {int(limit)}", user_id))


# ---------- conversation segments (episodic memory) ----------
async def add_segment(user_id: str, conv_id: str, from_seq: int, to_seq: int, summary: str,
                      embedding: list[float] | None) -> None:
    await pool().execute(
        "INSERT INTO conversation_segments(conversation_id, user_id, from_seq, to_seq, summary, embedding) "
        "VALUES ($1,$2,$3,$4,$5,$6::vector)", conv_id, user_id, from_seq, to_seq, summary, vec(embedding))


async def list_segments(user_id: str, conv_id: str | None = None) -> list[dict]:
    if conv_id:
        return rows(await pool().fetch(
            "SELECT id, conversation_id, from_seq, to_seq, summary, created_at FROM conversation_segments "
            "WHERE user_id=$1 AND conversation_id=$2 ORDER BY from_seq", user_id, conv_id))
    return rows(await pool().fetch(
        "SELECT s.id, s.conversation_id, c.title, s.from_seq, s.to_seq, s.summary, s.created_at "
        "FROM conversation_segments s JOIN conversations c ON c.id=s.conversation_id "
        "WHERE s.user_id=$1 ORDER BY s.conversation_id, s.from_seq", user_id))


async def search_segments(user_id: str, query: str, embedding: list[float] | None, *, conv_id: str | None,
                          include_other: bool, k: int = 6) -> tuple[list[dict], list[dict]]:
    """(vector hits, keyword hits) over this conversation's segments, plus other memory-enabled chats if allowed."""
    scope = "s.conversation_id=$2" if not include_other else "(s.conversation_id=$2 OR c.memory_enabled)"
    base = ("SELECT s.id, s.conversation_id, c.title, s.from_seq, s.to_seq, s.summary AS content, s.created_at "
            "{extra} FROM conversation_segments s JOIN conversations c ON c.id=s.conversation_id "
            f"WHERE s.user_id=$1 AND {scope} ")
    conv = conv_id or "00000000-0000-0000-0000-000000000000"
    vhits: list[dict] = []
    if embedding is not None:
        vhits = rows(await pool().fetch(
            base.format(extra=", 1 - (s.embedding <=> $3::vector) AS sim")
            + f"AND s.embedding IS NOT NULL ORDER BY s.embedding <=> $3::vector LIMIT {int(k)}",
            user_id, conv, vec(embedding)))
    khits: list[dict] = []
    q = or_query(query)
    if q:
        khits = rows(await pool().fetch(
            base.format(extra=", ts_rank(s.tsv, websearch_to_tsquery('english', $3)) AS kw")
            + f"AND s.tsv @@ websearch_to_tsquery('english', $3) ORDER BY kw DESC LIMIT {int(k)}",
            user_id, conv, q))
    return vhits, khits


async def clear_conversation_memory(user_id: str, conv_id: str) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.execute("DELETE FROM conversation_segments WHERE user_id=$1 AND conversation_id=$2", user_id, conv_id)
        await c.execute("UPDATE conversations SET summary=NULL, summarized_upto=0, state='{}'::jsonb "
                        "WHERE id=$1 AND user_id=$2", conv_id, user_id)


async def clear_all_conversation_memory(user_id: str) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.execute("DELETE FROM conversation_segments WHERE user_id=$1", user_id)
        await c.execute("UPDATE conversations SET summary=NULL, summarized_upto=0, state='{}'::jsonb WHERE user_id=$1",
                        user_id)


async def conversation_summaries(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, title, summary, summarized_upto, state, memory_enabled, created_at FROM conversations "
        "WHERE user_id=$1 AND (summary IS NOT NULL OR state <> '{}'::jsonb) ORDER BY created_at", user_id))


async def set_conversation_memory_enabled(user_id: str, conv_id: str, enabled: bool) -> None:
    await db.pool().execute("UPDATE conversations SET memory_enabled=$1 WHERE id=$2 AND user_id=$3",
                            enabled, conv_id, user_id)
