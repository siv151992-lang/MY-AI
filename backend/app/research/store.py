"""PostgreSQL persistence for research runs, sources, claims and progress events."""
from __future__ import annotations

from typing import Any

from ..db import pool, row, rows

MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS research_runs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question    TEXT NOT NULL,
  depth       TEXT NOT NULL DEFAULT 'standard',
  status      TEXT NOT NULL DEFAULT 'queued',
  model_id    TEXT,
  plan        JSONB NOT NULL DEFAULT '[]'::jsonb,
  analysis    JSONB NOT NULL DEFAULT '{}'::jsonb,
  stats       JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary     TEXT NOT NULL DEFAULT '',
  report      TEXT NOT NULL DEFAULT '',
  error       TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
)""",
    "CREATE INDEX IF NOT EXISTS research_runs_user ON research_runs(user_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS research_sources (
  id                 BIGSERIAL PRIMARY KEY,
  run_id             UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  n                  INTEGER,             -- S-number; only sources that were actually read get one
  url                TEXT NOT NULL,
  title              TEXT NOT NULL DEFAULT '',
  domain             TEXT NOT NULL,
  reliability        DOUBLE PRECISION NOT NULL,
  reliability_label  TEXT NOT NULL,
  reliability_reason TEXT NOT NULL,
  query              TEXT NOT NULL DEFAULT '',
  snippet            TEXT NOT NULL DEFAULT '',
  status             TEXT NOT NULL,       -- read | failed | irrelevant
  error              TEXT NOT NULL DEFAULT '',
  content            TEXT NOT NULL DEFAULT '',
  purpose            TEXT NOT NULL DEFAULT 'search',  -- search | verification
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, url)
)""",
    "CREATE INDEX IF NOT EXISTS research_sources_run ON research_sources(run_id, n)",
    """CREATE TABLE IF NOT EXISTS research_claims (
  id          BIGSERIAL PRIMARY KEY,
  run_id      UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  n           INTEGER NOT NULL,           -- C-number
  subq        INTEGER NOT NULL,
  text        TEXT NOT NULL,
  quote       TEXT NOT NULL,
  source      INTEGER NOT NULL,           -- S-number of the source the quote was verified in
  importance  TEXT NOT NULL DEFAULT 'medium',
  status      TEXT NOT NULL DEFAULT 'single_source',
  supporting  INTEGER[] NOT NULL DEFAULT '{}',
  conflicting INTEGER[] NOT NULL DEFAULT '{}',
  note        TEXT NOT NULL DEFAULT '',
  UNIQUE (run_id, n)
)""",
    """CREATE TABLE IF NOT EXISTS research_events (
  run_id     UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  type       TEXT NOT NULL,
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, seq)
)""",
]

RUN_COLS = ("id, question, depth, status, model_id, plan, analysis, stats, summary, report, error, created_at, "
            "updated_at, finished_at")
SRC_COLS = ("n, url, title, domain, reliability, reliability_label, reliability_reason, query, snippet, status, error, "
            "purpose, fetched_at")
CLAIM_COLS = "n, subq, text, quote, source, importance, status, supporting, conflicting, note"
TERMINAL = {"completed", "failed", "cancelled"}


async def create_run(user_id: str, question: str, depth: str, model_id: str | None) -> dict:
    return row(await pool().fetchrow(
        f"INSERT INTO research_runs(user_id, question, depth, model_id) VALUES ($1,$2,$3,$4) RETURNING {RUN_COLS}",
        user_id, question, depth, model_id))  # type: ignore[return-value]


async def get_run(user_id: str, run_id: str) -> dict | None:
    try:
        return row(await pool().fetchrow(f"SELECT {RUN_COLS} FROM research_runs WHERE id=$1 AND user_id=$2", run_id, user_id))
    except Exception:
        return None


async def list_runs(user_id: str) -> list[dict]:
    return rows(await pool().fetch(
        "SELECT id, question, depth, status, summary, error, created_at, finished_at, stats FROM research_runs "
        "WHERE user_id=$1 ORDER BY created_at DESC LIMIT 200", user_id))


async def update_run(run_id: str, **fields: Any) -> None:
    allowed = {"status", "plan", "analysis", "stats", "summary", "report", "error"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            args.append(v)
            sets.append(f"{k}=${len(args)}")
    if fields.get("status") in TERMINAL:
        sets.append("finished_at=now()")
    args.append(run_id)
    await pool().execute(f"UPDATE research_runs SET {', '.join(sets)}, updated_at=now() WHERE id=${len(args)}", *args)


async def delete_run(user_id: str, run_id: str) -> bool:
    return (await pool().execute("DELETE FROM research_runs WHERE id=$1 AND user_id=$2", run_id, user_id)).endswith(" 1")


async def mark_interrupted() -> int:
    res = await pool().execute(
        "UPDATE research_runs SET status='failed', error='Interrupted by a server restart before it finished.', "
        "finished_at=now() WHERE status NOT IN ('completed','failed','cancelled')")
    return int(res.split()[-1])


# ---------- sources ----------
async def add_source(run_id: str, **s: Any) -> None:
    await pool().execute(
        "INSERT INTO research_sources(run_id, n, url, title, domain, reliability, reliability_label, reliability_reason, "
        "query, snippet, status, error, content, purpose) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) "
        "ON CONFLICT (run_id, url) DO NOTHING",
        run_id, s.get("n"), s["url"], s.get("title", ""), s["domain"], s["reliability"], s["reliability_label"],
        s["reliability_reason"], s.get("query", ""), s.get("snippet", ""), s["status"], s.get("error", ""),
        s.get("content", ""), s.get("purpose", "search"))


async def sources(run_id: str, with_content: bool = False) -> list[dict]:
    cols = SRC_COLS + (", content" if with_content else "")
    return rows(await pool().fetch(
        f"SELECT {cols} FROM research_sources WHERE run_id=$1 ORDER BY n NULLS LAST, id", run_id))


async def set_source_status(run_id: str, n: int, status: str) -> None:
    await pool().execute("UPDATE research_sources SET status=$3 WHERE run_id=$1 AND n=$2", run_id, n, status)


# ---------- claims ----------
async def add_claims(run_id: str, claims: list[dict]) -> None:
    if claims:
        await pool().executemany(
            "INSERT INTO research_claims(run_id, n, subq, text, quote, source, importance, status, supporting) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            [(run_id, c["n"], c["subq"], c["text"], c["quote"], c["source"], c["importance"], c.get("status", "single_source"),
              c.get("supporting", [c["source"]])) for c in claims])


async def update_claim(run_id: str, n: int, **f: Any) -> None:
    sets, args = [], []
    for k in ("status", "supporting", "conflicting", "note"):
        if k in f:
            args.append(f[k])
            sets.append(f"{k}=${len(args)}")
    args += [run_id, n]
    await pool().execute(f"UPDATE research_claims SET {', '.join(sets)} WHERE run_id=${len(args) - 1} AND n=${len(args)}", *args)


async def claims(run_id: str) -> list[dict]:
    return rows(await pool().fetch(f"SELECT {CLAIM_COLS} FROM research_claims WHERE run_id=$1 ORDER BY n", run_id))


# ---------- events (used by autonomy.bus.EventBus) ----------
class EventStore:
    """Events are published concurrently (parallel searches/fetches), so sequence numbers are assigned under a
    per-run lock; the retry covers the rare case of another process writing to the same run."""
    _locks: dict[str, "asyncio.Lock"] = {}

    async def add_event(self, run_id: str, type_: str, data: dict) -> dict:
        import asyncio

        import asyncpg
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            for attempt in range(5):
                try:
                    r = await pool().fetchrow(
                        "INSERT INTO research_events(run_id, seq, type, data) VALUES ($1, "
                        "(SELECT COALESCE(MAX(seq),0)+1 FROM research_events WHERE run_id=$1), $2, $3) "
                        "RETURNING seq, type, data, created_at", run_id, type_, data)
                    break
                except asyncpg.UniqueViolationError:
                    if attempt == 4:
                        raise
        if data.get("final"):
            self._locks.pop(run_id, None)
        return {"seq": r["seq"], "type": r["type"], "data": r["data"], "at": r["created_at"].isoformat()}

    async def list_events(self, run_id: str, after: int = 0) -> list[dict]:
        rs = await pool().fetch("SELECT seq, type, data, created_at FROM research_events WHERE run_id=$1 AND seq>$2 "
                                "ORDER BY seq", run_id, after)
        return [{"seq": r["seq"], "type": r["type"], "data": r["data"], "at": r["created_at"].isoformat()} for r in rs]
