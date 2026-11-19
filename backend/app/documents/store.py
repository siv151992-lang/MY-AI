"""PostgreSQL persistence for documents (extends the existing files / document_chunks tables)."""
from __future__ import annotations

from typing import Any

from ..db import pool, row, rows, vec

MIGRATIONS = [
    """ALTER TABLE files
         ADD COLUMN IF NOT EXISTS doc_type TEXT,
         ADD COLUMN IF NOT EXISTS pages INTEGER,
         ADD COLUMN IF NOT EXISTS summary TEXT,
         ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb,
         ADD COLUMN IF NOT EXISTS ocr BOOLEAN NOT NULL DEFAULT false,
         ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT '',
         ADD COLUMN IF NOT EXISTS warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
         ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()""",
    """ALTER TABLE document_chunks
         ADD COLUMN IF NOT EXISTS page INTEGER,
         ADD COLUMN IF NOT EXISTS section TEXT NOT NULL DEFAULT ''""",
    """CREATE TABLE IF NOT EXISTS document_tables (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id   UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  idx       INTEGER NOT NULL,
  name      TEXT NOT NULL,
  source    TEXT NOT NULL,
  location  TEXT NOT NULL DEFAULT '',
  columns   JSONB NOT NULL,
  rows      JSONB NOT NULL,
  total_rows INTEGER NOT NULL,
  formulas  INTEGER NOT NULL DEFAULT 0,
  profile   JSONB NOT NULL DEFAULT '{}'::jsonb
)""",
    "CREATE INDEX IF NOT EXISTS document_tables_file ON document_tables(file_id, idx)",
    """CREATE TABLE IF NOT EXISTS document_access (
  file_id    UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       TEXT NOT NULL DEFAULT 'viewer',
  granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (file_id, user_id)
)""",
    "CREATE INDEX IF NOT EXISTS document_access_user ON document_access(user_id)",
]

DOC_COLS = ("f.id, f.filename, f.mime_type, f.size_bytes, f.kind, f.status, f.doc_type, f.pages, f.summary, f.meta, f.ocr, "
            "f.error, f.warnings, f.created_at, f.updated_at, f.user_id = $1 AS owned, "
            "(SELECT count(*) FROM document_chunks c WHERE c.file_id=f.id) AS chunks, "
            "(SELECT count(*) FROM document_tables t WHERE t.file_id=f.id) AS tables, "
            "(SELECT count(*) FROM document_access a2 WHERE a2.file_id=f.id) AS shared_with, "
            "CASE WHEN f.user_id <> $1 THEN (SELECT name FROM users u WHERE u.id=f.user_id) END AS owner_name")


async def list_docs(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        f"SELECT {DOC_COLS} FROM files f LEFT JOIN document_access a ON a.file_id=f.id AND a.user_id=$1 "
        "WHERE f.user_id=$1 OR a.user_id IS NOT NULL ORDER BY f.created_at DESC", user_id))


async def get_doc(user_id: str, file_id: str) -> dict | None:
    """Caller must have checked access; the owner flag is computed relative to user_id."""
    return row(await pool().fetchrow(f"SELECT {DOC_COLS}, f.storage_path, f.text_content FROM files f WHERE f.id=$2",
                                     user_id, file_id))


async def get_docs(user_id: str, file_ids: list[str]) -> list[dict]:
    if not file_ids:
        return []
    rs = rows(await pool().fetch(f"SELECT {DOC_COLS}, f.text_content FROM files f WHERE f.id = ANY($2::uuid[])", user_id, file_ids))
    order = {i: n for n, i in enumerate(file_ids)}
    return sorted(rs, key=lambda r: order.get(r["id"], 0))


async def update_doc(file_id: str, **f: Any) -> None:
    allowed = {"status", "doc_type", "pages", "summary", "meta", "ocr", "error", "warnings", "text_content", "filename"}
    sets, args = [], []
    for k, v in f.items():
        if k in allowed:
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    args.append(file_id)
    await pool().execute(f"UPDATE files SET {', '.join(sets)}, updated_at=now() WHERE id=${len(args)}", *args)


async def replace_chunks(owner_id: str, file_id: str, chunks: list, embeddings: list[list[float]] | None) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.execute("DELETE FROM document_chunks WHERE file_id=$1", file_id)
        await c.executemany(
            "INSERT INTO document_chunks(file_id, user_id, idx, content, embedding, page, section) "
            "VALUES ($1,$2,$3,$4,$5::vector,$6,$7)",
            [(file_id, owner_id, i, ch.content, vec(embeddings[i]) if embeddings else None, ch.page, ch.section[:300])
             for i, ch in enumerate(chunks)])


async def replace_tables(file_id: str, tables: list, profiles: list[dict]) -> None:
    async with pool().acquire() as c, c.transaction():
        await c.execute("DELETE FROM document_tables WHERE file_id=$1", file_id)
        await c.executemany(
            "INSERT INTO document_tables(file_id, idx, name, source, location, columns, rows, total_rows, formulas, profile) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            [(file_id, i, t.name[:200], t.source, t.location[:200], t.columns, t.rows, t.total_rows, t.formulas, p)
             for i, (t, p) in enumerate(zip(tables, profiles))])


async def tables(file_id: str, with_rows: bool = False) -> list[dict]:
    cols = "id, idx, name, source, location, columns, total_rows, formulas, profile" + (", rows" if with_rows else "")
    return rows(await pool().fetch(f"SELECT {cols} FROM document_tables WHERE file_id=$1 ORDER BY idx", file_id))


async def table(file_id: str, table_id: str) -> dict | None:
    try:
        return row(await pool().fetchrow(
            "SELECT id, idx, name, source, location, columns, rows, total_rows, formulas, profile FROM document_tables "
            "WHERE file_id=$1 AND id=$2", file_id, table_id))
    except Exception:
        return None


async def chunks(file_id: str, limit: int = 2000) -> list[dict]:
    return rows(await pool().fetch(
        f"SELECT idx, content, page, section FROM document_chunks WHERE file_id=$1 ORDER BY idx LIMIT {int(limit)}", file_id))


async def missing_embeddings(limit: int = 200) -> list[dict]:
    return rows(await pool().fetch(f"SELECT id, content FROM document_chunks WHERE embedding IS NULL LIMIT {int(limit)}"))


async def set_chunk_embedding(chunk_id: int, emb: list[float]) -> None:
    await pool().execute("UPDATE document_chunks SET embedding=$2::vector WHERE id=$1", chunk_id, vec(emb))
