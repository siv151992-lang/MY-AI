"""End-to-end tests of the platform hub against Postgres with scripted model providers (no network)."""
import asyncio
import json
import os
import time
import uuid

import httpx
import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
H = {"X-Requested-With": "aria"}


class FakeProvider:
    """Streams like a real provider. Records system prompts; `fail` makes every call error out."""

    def __init__(self, name, fail=False):
        self.name, self.fail, self.systems = name, fail, []

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
        from app.llm.formats import ProviderError
        self.systems.append(system)
        if self.fail:
            raise ProviderError(f"{self.name} is down", 503)
        names = [t["name"] for t in tools or []]
        last = messages[-1]["content"]
        text = " ".join(p.get("text", "") for p in last if isinstance(p, dict) and p.get("type") == "text")
        if "submit_result" in names:
            yield {"type": "tool_call", "id": "s1", "name": "submit_result",
                   "input": {"status": "done", "result": f"[{self.name}:{model}] done: {text[:120]}"}}
            yield {"type": "done", "stop_reason": "tool_use", "usage": {"input_tokens": 11, "output_tokens": 5}}
            return
        if temperature is not None and not tools:  # utility/complete() calls
            yield {"type": "text", "text": "{}"}
        else:
            yield {"type": "text", "text": f"Answer from {self.name}/{model}."}
        yield {"type": "done", "stop_reason": "end_turn", "usage": {"input_tokens": 20, "output_tokens": 8}}

    async def complete(self, *, model, system, messages, max_tokens=1024, **_):
        out = []
        async for ev in self.stream(model=model, system=system, messages=messages, max_tokens=max_tokens, temperature=0.2):
            if ev["type"] == "text":
                out.append(ev["text"])
        return "".join(out)


async def _admin(sql):
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        await c.execute(sql)
    finally:
        await c.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    name = f"aria_hub_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    for k, v in {"DATABASE_URL": DSN.rsplit("/", 1)[0] + "/" + name, "JWT_SECRET": "x" * 40,
                 "UPLOAD_DIR": str(tmp_path / "up"), "WORKSPACE_ROOT": str(tmp_path / "ws"), "SANDBOX_URL": "off",
                 "RATE_LIMIT_PER_MINUTE": "1000", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "OLLAMA_BASE_URL": "",
                 "TAVILY_API_KEY": "", "BRAVE_API_KEY": "", "DEFAULT_MODEL_ID": "claude-sonnet",
                 "UTILITY_MODEL_ID": "claude-haiku", "SLACK_WEBHOOK_URL": "", "EMBEDDING_PROVIDER": ""}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    from app.config import get_settings
    from app.hub import integrations, usage
    from app.llm import registry
    fakes = {"anthropic": FakeProvider("anthropic"), "openai": FakeProvider("openai")}
    get_settings.cache_clear()
    registry.available_models.cache_clear()
    monkeypatch.setattr(registry, "providers", lambda: {k: usage.MeteredProvider(v) for k, v in fakes.items()})
    usage.reset_health_cache()

    # integrations: pretend api.example.com is public and answer from an in-process handler
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path.endswith("/fail"):
            return httpx.Response(500, text="nope")
        return httpx.Response(200, json={"path": req.url.path, "method": req.method, "auth": req.headers.get("authorization"),
                                         "echo": "secret-token-1 should be hidden"})
    monkeypatch.setattr(integrations, "_is_public", lambda url: "example.com" in url)
    monkeypatch.setattr(integrations, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/signup", json={"email": "hub@example.com", "name": "Hub", "password": "password1234"},
                      headers=H).status_code == 200
        yield c, fakes, calls
    registry.available_models.cache_clear()
    get_settings.cache_clear()
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _chat(c, conv_id, text, **kw):
    r = c.post(f"/api/conversations/{conv_id}/messages", json={"content": text, **kw}, headers=H)
    assert r.status_code == 200, r.text
    events = []
    for block in r.text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.strip().split("\n") if ": " in l)
        if "data" in lines:
            events.append(json.loads(lines["data"]))
    return events


def _wait_until(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_auto_routing_fallback_projects_agents_and_analytics(env):
    c, fakes, _ = env
    # adaptive routing: explanation endpoint
    r = c.post("/api/router/route", json={"text": "hello"}, headers=H).json()
    assert r["model_id"] in ("claude-haiku", "gpt-mini") and r["complexity"] == "low"
    hard = c.post("/api/router/route", json={"text": "Prove step by step and derive the formula in depth with a thorough "
                                                     "proof; evaluate edge cases comprehensively."}, headers=H).json()
    assert hard["model_id"] in ("claude-opus", "openai-reasoning") and hard["complexity"] == "high"

    # project with instructions → auto-routed chat sees them; anthropic fails so it falls back to openai
    p = c.post("/api/projects", json={"name": "Launch", "instructions": "Always answer in French."}, headers=H).json()
    conv = c.post("/api/conversations", json={"model_id": "auto", "project_id": p["id"]}, headers=H).json()
    c.put("/api/router/prefs", json={"priority": "balanced", "pinned": {"chat": "claude-haiku"}}, headers=H)
    fakes["anthropic"].fail = True
    ev = _chat(c, conv["id"], "hi there")
    kinds = [e["type"] for e in ev]
    assert "route" in kinds and "route_fallback" in kinds, kinds
    done = next(e for e in ev if e["type"] == "done")["message"]
    assert done["meta"]["route"]["fallbacks_used"][0]["model"] == "claude-haiku"
    assert done["meta"]["model"].startswith("gpt") and done["meta"]["project_id"] == p["id"]
    assert any("Always answer in French." in s for s in fakes["openai"].systems)
    fakes["anthropic"].fail = False
    proj = c.get(f"/api/projects/{p['id']}").json()
    assert [x["id"] for x in proj["items"]["conversation"]] == [conv["id"]]

    # custom agent: chat with it (persona + model) and give it a task
    a = c.post("/api/agents", json={"name": "Contract Reviewer", "instructions": "Review contracts for risky clauses.",
                                    "tools": [], "model_id": "claude-opus"}, headers=H).json()
    aconv = c.post("/api/conversations", json={"agent_id": a["id"]}, headers=H).json()
    ev = _chat(c, aconv["id"], "Check clause 4")
    assert next(e for e in ev if e["type"] == "done")["message"]["meta"]["agent"]["name"] == "Contract Reviewer"
    assert any("Review contracts for risky clauses." in s for s in fakes["anthropic"].systems)
    res = c.post(f"/api/agents/{a['id']}/run", json={"task": "Summarise the NDA risks"}, headers=H).json()
    assert res["status"] == "done" and "claude-opus" in res["result"] and res["model"] == "claude-opus"
    team = c.get("/api/team/agents").json()
    assert any(x.get("custom") and x["label"].startswith("Contract Reviewer") for x in team)

    # skills: built-in + custom, run directly and applied to a chat message
    sk = c.post("/api/skills", json={"name": "Haiku poem", "instructions": "Write a haiku about {{topic}}"}, headers=H).json()
    assert [i["name"] for i in sk["inputs"]] == ["topic"]
    bad = c.post(f"/api/skills/{sk['id']}/run", json={"values": {}}, headers=H)
    assert bad.status_code == 422
    out = c.post(f"/api/skills/{sk['id']}/run", json={"values": {"topic": "autumn"}}, headers=H).json()
    assert out["status"] == "done" and "Write a haiku about autumn" in out["result"]
    assert any(s["id"] == "builtin:summarize" for s in c.get("/api/skills").json())
    ev = _chat(c, aconv["id"], "My notes...", skill_id="builtin:summarize")
    assert next(e for e in ev if e["type"] == "done")["message"]["meta"]["skill"]["name"] == "Summarize"

    # analytics: every call above was metered and attributed
    from app.hub import usage
    asyncio.run(asyncio.sleep(0))
    an = _wait_until(lambda: (lambda d: d if d["totals"]["calls"] >= 5 else None)(c.get("/api/analytics?days=7").json()))
    feats = {f["feature"] for f in an["by_feature"]}
    assert {"chat", "skill", "custom_agent"} <= feats, feats
    assert an["totals"]["errors"] >= 1 and an["routing"]["with_fallback"] >= 1
    assert any(m["model_id"] == "claude-haiku" and m["errors"] >= 1 for m in an["by_model"])
    assert an["totals"]["cost_usd"] is None and an["totals"]["cost_note"]
    assert c.get("/api/analytics?scope=all").status_code == 200  # first account is the owner


def test_context_aware_memory_is_scoped_to_projects(env):
    c, _, _ = env
    from app import db
    from app.memory import store

    p = c.post("/api/projects", json={"name": "Secret project"}, headers=H).json()
    uid = c.get("/api/auth/me").json()["id"]

    async def go():
        if True:
            kw = dict(category="fact", status="active", source="user", importance=0.9, confidence=1.0,
                      sensitivity="normal", pinned=True, embedding=None, conv_id=None, excerpt=None,
                      supersedes_id=None, changed_by="user")
            store.PROJECT_SCOPE.set(p["id"])
            await store.insert(uid, content="Codename is BLUEBIRD", **kw)
            store.PROJECT_SCOPE.set(None)
            await store.insert(uid, content="Prefers metric units", **kw)
            outside = {m["content"] for m in await store.pinned(uid)}
            store.PROJECT_SCOPE.set(p["id"])
            inside = {m["content"] for m in await store.pinned(uid)}
            kw_hits = {m["content"] for m in await store.keyword_search(uid, "codename bluebird", ("active",), 5)}
            store.PROJECT_SCOPE.set(None)
            kw_out = await store.keyword_search(uid, "codename bluebird", ("active",), 5)
            return outside, inside, kw_hits, kw_out
    outside, inside, kw_hits, kw_out = c.portal.call(go)  # run in the app's event loop, with its pool
    assert outside == {"Prefers metric units"}
    assert inside == {"Prefers metric units", "Codename is BLUEBIRD"}
    assert kw_hits == {"Codename is BLUEBIRD"} and kw_out == []
    # the memory manager (no scope) still shows everything
    assert len(c.get("/api/memories").json()["items"]) == 2


def test_integrations_automations_webhooks_and_tasks(env):
    c, _, calls = env
    # integration: secret encrypted, never returned, injected on calls, redacted in output
    bad = c.post("/api/integrations", json={"name": "Local", "base_url": "http://127.0.0.1:9000"}, headers=H)
    assert bad.status_code == 422
    it = c.post("/api/integrations", json={"name": "CRM", "base_url": "https://api.example.com/v1", "auth_type": "bearer",
                                           "secret": "secret-token-1", "test_path": "/ping", "allow_writes": True},
                headers=H).json()
    assert it["has_secret"] and "secret" not in json.dumps(it).replace("has_secret", "") and it["tool_name"] == "api_crm"
    t = c.post(f"/api/integrations/{it['id']}/test", headers=H).json()
    assert t["ok"] and t["status"] == 200 and "secret-token-1" not in t["preview"]
    assert calls[-1].headers["authorization"] == "Bearer secret-token-1"
    lst = c.get("/api/integrations").json()
    assert any(b["name"] == "Slack" and not b["configured"] for b in lst["builtin"])
    assert any(t["name"] == "api_crm" for t in c.get("/api/tools").json())

    # manual workflow: AI step -> integration POST (writes allowed) -> Slack (not configured → clear failure)
    auto = c.post("/api/automations", json={"name": "Sync", "steps": [
        {"type": "prompt", "instructions": "Draft a note for {{date}}"},
        {"type": "http", "integration_id": it["id"], "method": "POST", "path": "/notes", "body": '{"ok": true}'},
        {"type": "notify", "channel": "slack", "message": "{{prev}}"}]}, headers=H).json()
    run = c.post(f"/api/automations/{auto['id']}/run", headers=H).json()
    done = _wait_until(lambda: (lambda r: r if r["status"] != "running" else None)(
        c.get(f"/api/automation-runs/{run['id']}").json()))
    assert done["status"] == "failed" and "Slack isn't configured" in done["error"]
    assert [s["status"] for s in done["steps"]] == ["succeeded", "succeeded", "failed"]
    assert calls[-1].method == "POST" and calls[-1].url.path == "/v1/notes"

    # webhook trigger: secret URL works once known, wrong token is 404
    hook = c.post("/api/automations", json={"name": "On event", "trigger_type": "webhook", "steps": [
        {"type": "prompt", "instructions": "Event: {{payload.kind}}"}]}, headers=H).json()
    url = hook["webhook_url"]
    path = url.split("://", 1)[1].split("/", 1)[1]
    assert c.post(f"/{path[:-3]}xyz", json={"kind": "x"}).status_code == 404
    r = c.post(f"/{path}", json={"kind": "signup"})  # no CSRF header: called by other services
    assert r.status_code == 200, r.text
    wr = _wait_until(lambda: (lambda x: x if x["status"] != "running" else None)(
        c.get(f"/api/automation-runs/{r.json()['run_id']}").json()))
    assert wr["status"] == "succeeded" and "Event: signup" in wr["output"] and wr["trigger"] == "webhook"

    # scheduled: due automations are picked up by the scheduler tick exactly once
    sch = c.post("/api/automations", json={"name": "Daily", "trigger_type": "schedule", "schedule": "@every 10m",
                                           "steps": [{"type": "prompt", "instructions": "tick"}]}, headers=H).json()
    assert sch["next_run_at"] and "every 10 minutes" in sch["schedule_text"]
    from app import db
    from app.hub.automations import get_runner

    async def tick():
        if True:
            await db.pool().execute("UPDATE automations SET next_run_at = now() - interval '1 minute' WHERE id=$1", sch["id"])
            first = await get_runner().tick()
            second = await get_runner().tick()
            await asyncio.sleep(0.5)
            return first, second
    first, second = c.portal.call(tick)
    assert (first, second) == (1, 0)

    # task monitor lists automation runs with progress; cancel of a finished run is refused
    snap = c.get("/api/tasks").json()
    assert any(t["kind"] == "automation" and t["progress"]["total"] == 3 for t in snap["recent"])
    assert c.post(f"/api/tasks/automation/{run['id']}/cancel", headers=H).status_code == 409
    assert c.get("/api/voice/status").json()["server_transcription"] is False
