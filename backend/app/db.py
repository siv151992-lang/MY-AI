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
        "WHERE user_id=$1 ORDER BY updated_at DESC LIMIT $2", user_id, limit))


async def create_conversation(user_id: str, model_id: str | None) -> dict:
    return row(await pool().fetchrow(
        "INSERT INTO conversations(user_id, model_id) VALUES ($1,$2) RETURNING id, title, model_id, created_at, updated_at",
        user_id, model_id))  # type: ignore[return-value]


async def get_conversation(user_id: str, conv_id: str) -> dict | None:
    return row(await pool().fetchrow("SELECT * FROM conversations WHERE id=$1 AND user_id=$2", conv_id, user_id))


async def update_conversation(user_id: str, conv_id: str, **fields: Any) -> None:
    allowed = {"title", "model_id", "summary", "summarized_upto"}
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


async def get_files(user_id: str, file_ids: list[str]) -> list[dict]:
    if not file_ids:
        return []
    return rows(await pool().fetch("SELECT * FROM files WHERE user_id=$1 AND id = ANY($2::uuid[])", user_id, file_ids))


async def list_files(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, filename, mime_type, size_bytes, kind, status, created_at FROM files WHERE user_id=$1 "
        "ORDER BY created_at DESC", user_id))


async def delete_file(user_id: str, file_id: str) -> dict | None:
    return row(await pool().fetchrow("DELETE FROM files WHERE id=$1 AND user_id=$2 RETURNING storage_path",
                                     file_id, user_id))


async def add_chunks(user_id: str, file_id: str, chunks: list[str], embeddings: list[list[float]] | None) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.executemany(
            "INSERT INTO document_chunks(file_id, user_id, idx, content, embedding) VALUES ($1,$2,$3,$4,$5::vector)",
            [(file_id, user_id, i, ch, vec(embeddings[i]) if embeddings else None) for i, ch in enumerate(chunks)])


async def search_chunks(user_id: str, query: str, embedding: list[float] | None, file_ids: list[str] | None,
                        k: int = 6) -> list[dict]:
    """Hybrid retrieval: vector similarity when embeddings exist, else Postgres full-text search."""
    scope = "AND c.file_id = ANY($3::uuid[])" if file_ids else ""
    if embedding is not None:
        sql = (f"SELECT c.content, c.idx, f.filename, 1 - (c.embedding <=> $2::vector) AS score "
               f"FROM document_chunks c JOIN files f ON f.id=c.file_id "
               f"WHERE c.user_id=$1 AND c.embedding IS NOT NULL {scope} ORDER BY c.embedding <=> $2::vector LIMIT {int(k)}")
        args: list[Any] = [user_id, vec(embedding)]
    else:
        sql = (f"SELECT c.content, c.idx, f.filename, ts_rank(c.tsv, q) AS score "
               f"FROM document_chunks c JOIN files f ON f.id=c.file_id, websearch_to_tsquery('english', $2) q "
               f"WHERE c.user_id=$1 {scope} AND c.tsv @@ q ORDER BY score DESC LIMIT {int(k)}")
        args = [user_id, or_query(query)]
    if file_ids:
        args.append(file_ids)
    return rows(await pool().fetch(sql, *args))


# ---------- memories ----------
async def list_memories(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, content, created_at FROM memories WHERE user_id=$1 ORDER BY created_at DESC", user_id))


async def add_memory(user_id: str, content: str, embedding: list[float] | None, conv_id: str | None) -> dict:
    return row(await pool().fetchrow(
        "INSERT INTO memories(user_id, content, embedding, source_conversation_id) VALUES ($1,$2,$3::vector,$4) "
        "RETURNING id, content, created_at", user_id, content, vec(embedding), conv_id))  # type: ignore[return-value]


async def delete_memory(user_id: str, mem_id: str) -> bool:
    return (await pool().execute("DELETE FROM memories WHERE id=$1 AND user_id=$2", mem_id, user_id)).endswith("1")


async def search_memories(user_id: str, query: str, embedding: list[float] | None, k: int = 8) -> list[dict]:
    if embedding is not None:
        return rows(await pool().fetch(
            f"SELECT id, content, 1 - (embedding <=> $2::vector) AS score FROM memories "
            f"WHERE user_id=$1 AND embedding IS NOT NULL ORDER BY embedding <=> $2::vector LIMIT {int(k)}",
            user_id, vec(embedding)))
    return rows(await pool().fetch(
        f"SELECT id, content, ts_rank(tsv, q) AS score FROM memories, websearch_to_tsquery('english', $2) q "
        f"WHERE user_id=$1 AND (tsv @@ q OR $2 = '') ORDER BY score DESC, created_at DESC LIMIT {int(k)}",
        user_id, or_query(query)))
