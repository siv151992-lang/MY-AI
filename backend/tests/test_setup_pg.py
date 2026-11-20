"""Connecting a model from the chat screen: owner-only, checked, stored in the database and used at once."""
import asyncio
import os
import uuid

import httpx
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
def app_env(tmp_path, monkeypatch):
    name = f"aria_setup_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    for k, v in {"DATABASE_URL": DSN.rsplit("/", 1)[0] + "/" + name, "JWT_SECRET": "x" * 40,
                 "UPLOAD_DIR": str(tmp_path / "up"), "WORKSPACE_ROOT": str(tmp_path / "ws"), "SANDBOX_URL": "off",
                 "RATE_LIMIT_PER_MINUTE": "1000"}.items():
        monkeypatch.setenv(k, v)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL", "TAVILY_API_KEY", "BRAVE_API_KEY",
              "DEFAULT_MODEL_ID", "UTILITY_MODEL_ID", "EMBEDDING_PROVIDER"):
        monkeypatch.setenv(k, "")
    monkeypatch.chdir(tmp_path)  # no .env file is picked up
    from app.config import get_settings
    from app.llm import registry

    def reset():
        get_settings.cache_clear()
        registry.providers.cache_clear()
        registry.available_models.cache_clear()
    reset()
    yield
    reset()
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_paste_key_in_chat_connects_model(app_env, monkeypatch):
    from fastapi.testclient import TestClient

    from app import setup
    from app.config import get_settings
    from app.main import app
    calls = []

    async def fake_get(url, headers):
        calls.append(url)
        ok = headers.get("x-api-key") == "sk-ant-good-key-123"
        return httpx.Response(200 if ok else 401, request=httpx.Request("GET", url))
    monkeypatch.setattr(setup, "_get", fake_get)

    with TestClient(app) as c:
        sessions = {}
        for who in ("owner", "guest"):
            r = c.post("/api/auth/signup", json={"email": f"{who}@example.com", "name": who, "password": "password1234"},
                       headers=H)
            assert r.status_code == 200
            sessions[who] = c.cookies.get("aria_session")

        def as_(who):
            c.cookies.set("aria_session", sessions[who])
            return c
        owner, other = (lambda: as_("owner")), (lambda: as_("guest"))
        st = owner().get("/api/setup").json()
        assert st["configured"] is False and st["can_edit"] is True and owner().get("/api/models").json()["models"] == []
        assert other().get("/api/setup").json()["can_edit"] is False

        # only the owner may change keys
        assert other().post("/api/setup/key", json={"key": "sk-ant-good-key-123"}, headers=H).status_code == 403
        # junk and rejected keys are refused with a clear message, and nothing is stored
        r = owner().post("/api/setup/key", json={"key": "hello"}, headers=H)
        assert r.status_code == 422 and "doesn't look like an API key" in r.json()["detail"]
        r = owner().post("/api/setup/key", json={"key": "sk-ant-wrong-key-999"}, headers=H)
        assert r.status_code == 422 and "rejected" in r.json()["detail"]
        assert owner().get("/api/setup").json()["configured"] is False

        r = owner().post("/api/setup/key", json={"key": "sk-ant-good-key-123"}, headers=H)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] and body["provider"] == "anthropic" and body["default_model"] == "claude-sonnet"
        anth = next(p for p in body["providers"] if p["name"] == "anthropic")
        assert anth["source"] == "app" and "good-key" not in anth["hint"]
        assert calls[-1].endswith("/v1/models")
        models = owner().get("/api/models").json()
        assert models["default"] == "claude-sonnet" and {m["id"] for m in models["models"]} >= {"claude-sonnet", "claude-haiku"}
        assert other().get("/api/models").json()["default"] == "claude-sonnet"  # everyone can chat now

        # web search key
        r = owner().post("/api/setup/key", json={"key": "tvly-abc12345"}, headers=H)
        assert r.status_code == 200 and r.json()["web_search"] is True

    # survives a restart: the saved key is loaded from the database on startup
    get_settings.cache_clear()
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": "password1234"},
                      headers=H).status_code == 200
        assert c.get("/api/models").json()["default"] == "claude-sonnet"
        assert get_settings().anthropic_api_key == "sk-ant-good-key-123" and get_settings().tavily_api_key == "tvly-abc12345"
        assert c.delete("/api/setup/key/anthropic", headers=H).json()["configured"] is False
        assert c.delete("/api/setup/key/openai", headers=H).status_code == 409


def test_chat_works_in_basic_mode_without_any_model(app_env):
    """No key and no local AI: chat still answers simple things from the built-in knowledge base."""
    import json as _json

    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/signup", json={"email": "b@example.com", "name": "Bo", "password": "password1234"},
                      headers=H).status_code == 200
        conv = c.post("/api/conversations", json={}, headers=H).json()
        r = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "what is 15% of 80"}, headers=H)
        assert r.status_code == 200, r.text
        events = [_json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
        done = next(e for e in events if e["type"] == "done")["message"]
        assert "**12**" in done["content"] and done["meta"]["basic"] is True
        r = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "capital of Japan?"}, headers=H)
        assert "**Tokyo**" in r.text
        msgs = c.get(f"/api/conversations/{conv['id']}").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
