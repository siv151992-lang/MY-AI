"""Native (no-Docker) mode: a fresh database without pgvector and no code sandbox.

The backend must create the whole schema itself, fall back to full-text search, and switch code execution off
cleanly. Needs TEST_DATABASE_URL (a server where we may CREATE DATABASE)."""
import asyncio
import json
import os
import uuid

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
H = {"X-Requested-With": "aria"}


async def _admin(sql: str) -> None:
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        await c.execute(sql)
    finally:
        await c.close()


@pytest.fixture()
def fresh_db():
    name = f"aria_native_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    yield DSN.rsplit("/", 1)[0] + "/" + name
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_migrations_without_pgvector_are_complete_and_idempotent(fresh_db):
    from app import db

    async def go():
        await db.connect(fresh_db)
        try:
            await db.migrate(1536, disable_pgvector=True)
            await db.migrate(1536, disable_pgvector=True)  # second run: everything IF NOT EXISTS
            assert db.VECTOR_ENABLED is False
            p = db.pool()
            assert await p.fetchval("SELECT 1 FROM pg_extension WHERE extname='vector'") is None
            assert await p.fetchval("SELECT typtype::text FROM pg_type WHERE typname='vector'") == "d"  # stand-in domain
            tables = {r["tablename"] for r in await p.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
            for t in ("users", "conversations", "messages", "files", "document_chunks", "memories", "memory_revisions",
                      "workspaces", "research_runs", "document_tables", "document_access", "team_runs", "agent_runs"):
                assert t in tables, t
            idx = {r["indexname"] for r in await p.fetch("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
            assert "memories_embedding" not in idx and "memories_tsv" in idx
        finally:
            await db.close()
            db.VECTOR_ENABLED = True
    asyncio.run(go())


def test_app_works_without_pgvector_or_sandbox(fresh_db, tmp_path, monkeypatch):
    for k, v in {"DATABASE_URL": fresh_db, "JWT_SECRET": "x" * 40, "UPLOAD_DIR": str(tmp_path / "up"),
                 "WORKSPACE_ROOT": str(tmp_path / "ws"), "SANDBOX_URL": "off", "DISABLE_PGVECTOR": "true",
                 "EMBEDDING_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test-not-used", "RATE_LIMIT_PER_MINUTE": "1000"}.items():
        monkeypatch.setenv(k, v)
    from app import db
    from app.config import get_settings
    from app.llm import registry
    get_settings.cache_clear()
    registry.providers.cache_clear()
    registry.available_models.cache_clear()

    class Scripted:
        async def complete(self, *, model, system, messages, max_tokens=1000, **_):
            return "Invoices are due in 30 days [P1]." if system.startswith("Answer the user's question") else "Summary."
    from app.documents import service as docs
    monkeypatch.setattr(docs, "_models", lambda: ((Scripted(), "m"), (Scripted(), "u")))
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.chat import build_tools
    try:
        with TestClient(app) as c:
            assert db.VECTOR_ENABLED is False and get_settings().sandbox_url is None
            assert c.post("/api/auth/signup", json={"email": "native@example.com", "name": "N", "password": "password1234"},
                          headers=H).status_code == 200
            # documents: indexed without embeddings, found by full-text search
            up = c.post("/api/documents", files={"file": ("terms.txt", b"Invoices are due within 30 days of delivery.", "text/plain")}, headers=H)
            assert up.status_code == 200, up.text
            ans = c.post("/api/documents/ask", json={"question": "when are invoices due"}, headers=H).json()
            assert ans["citations"] and "30 days" in ans["citations"][0]["snippet"]
            # memory: create, dedupe and search with keyword search only
            assert c.post("/api/memories", json={"content": "Prefers dark mode in every app"}, headers=H).status_code == 200
            assert c.post("/api/memories", json={"content": "prefers dark mode in every app"}, headers=H).json()["duplicate"]
            hits = c.get("/api/memories", params={"q": "dark mode"}).json()["items"]
            assert hits[0]["content"] == "Prefers dark mode in every app"
            # code execution is off: no run_python tool, and workspace commands explain why
            assert "run_python" not in build_tools().names()
            ws = c.post("/api/workspaces", json={"name": "Native"}, headers=H).json()
            r = c.post(f"/api/workspaces/{ws['id']}/exec", json={"command": "ls"}, headers=H)
            assert r.status_code == 422 and "No sandbox is configured" in r.json()["detail"]
            assert c.get(f"/api/workspaces/{ws['id']}/git/log").json()[0]["message"] == "Initial commit"
    finally:
        db.VECTOR_ENABLED = True
        get_settings.cache_clear()
        registry.providers.cache_clear()
        registry.available_models.cache_clear()


def test_missing_pgvector_is_detected_automatically(fresh_db):
    """Without the disable flag: when CREATE EXTENSION vector fails (as on a server without pgvector), the
    backend must fall back by itself."""
    import asyncpg

    from app import db

    class NoVectorConn:
        def __init__(self, c):
            self.c = c

        async def execute(self, sql, *a):
            if "CREATE EXTENSION IF NOT EXISTS vector" in sql:
                raise asyncpg.exceptions.UndefinedFileError('could not open extension control file "vector.control"')
            return await self.c.execute(sql, *a)

        async def fetchval(self, sql, *a):
            return await self.c.fetchval(sql, *a)

    async def go():
        c = await asyncpg.connect(fresh_db)
        try:
            assert await db.ensure_extensions(NoVectorConn(c)) is False and db.VECTOR_ENABLED is False
            assert await db.ensure_extensions(NoVectorConn(c)) is False   # second start: domain already there
            await c.execute("CREATE TABLE t (e vector)")
            await c.execute("INSERT INTO t VALUES ($1::vector)", None)   # the casts used by the app keep working
        finally:
            await c.close()
            db.VECTOR_ENABLED = True
    asyncio.run(go())
