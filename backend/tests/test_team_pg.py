"""Multi-agent runs through the API with Postgres. Needs TEST_DATABASE_URL (schema from db/init/01-schema.sh)."""
import json
import os
import time
import uuid

import pytest

from .team_fakes import TeamLLM, recording_pool

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")
H = {"X-Requested-With": "aria"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for k, v in {"DATABASE_URL": DSN, "JWT_SECRET": "x" * 40, "UPLOAD_DIR": str(tmp_path / "up"), "WORKSPACE_ROOT": str(tmp_path / "ws"),
                 "RATE_LIMIT_PER_MINUTE": "1000"}.items():
        monkeypatch.setenv(k, v)
    from app.config import get_settings
    get_settings.cache_clear()
    from app.team.manager import get_manager
    m = get_manager()
    pool, _ = recording_pool(0.05)
    monkeypatch.setattr(m, "pool", lambda: pool)
    monkeypatch.setattr(m, "models", lambda mid: ((TeamLLM(), "m"), (TeamLLM(), "u"), None))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.manager, c.emails = m, {}
        for who in ("ann", "ben"):
            c.emails[who] = f"{who}-{uuid.uuid4().hex[:6]}@example.com"
            c.post("/api/auth/signup", json={"email": c.emails[who], "name": who, "password": "password1234"}, headers=H)
        yield c
    get_settings.cache_clear()


def login(c, who):
    c.cookies.clear()
    c.post("/api/auth/login", json={"email": c.emails[who], "password": "password1234"}, headers=H)


def wait(c, rid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/team/runs/{rid}").json()
        if not r["active"] and r["status"] in ("completed", "completed_with_issues", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_team_run_end_to_end(client):
    c = client
    login(c, "ann")
    agents = {a["name"]: a for a in c.get("/api/team/agents").json()}
    assert set(agents) == {"planning", "research", "coding", "data_analyst", "writing", "browser", "verification"}
    assert agents["writing"]["side_effects"] is True and agents["research"]["available"] is True
    rid = c.post("/api/team/runs", json={"goal": "Write a brief on Testland's population"}, headers=H).json()["id"]
    run = wait(c, rid)
    assert run["status"] == "completed", run["error"]
    assert [t["agent"] for t in run["tasks"]] == ["research", "data_analyst", "coding", "writing"]
    assert all(t["status"] == "done" and t["started_at"] and t["finished_at"] for t in run["tasks"])
    assert run["tasks"][3]["depends_on"] == ["T1", "T2"]
    assert run["tasks"][0]["sources"] == ["https://stats.test/pop"]
    assert run["conflicts"][0]["status"] == "resolved" and run["verification"]["ok"] is True
    assert run["final"].startswith("Testland has 5.2 million")
    evs = [json.loads(l[6:]) for l in c.get(f"/api/team/runs/{rid}/events").text.splitlines() if l.startswith('data: {"seq"')]
    types = [e["type"] for e in evs]
    for t in ("plan", "agent_status", "task_status", "agent_tool_start", "conflicts", "conflict_resolved", "verification", "final"):
        assert t in types, t
    assert types.index("plan") < types.index("task_status") < types.index("final_draft") < types.index("final")
    md = c.get(f"/api/team/runs/{rid}/export").text
    assert md.startswith("Testland has 5.2 million") and "T1 · Research Agent" in md and "Verification: passed" in md
    login(c, "ben")                                  # runs are private to their owner
    assert c.get(f"/api/team/runs/{rid}").status_code == 404
    assert c.get(f"/api/team/runs/{rid}/events").status_code == 404
    assert c.get("/api/team/runs").json() == []


def test_cancel_and_unavailable_model(client, monkeypatch):
    c = client
    login(c, "ann")
    slow, _ = recording_pool(2.0)
    monkeypatch.setattr(c.manager, "pool", lambda: slow)
    rid = c.post("/api/team/runs", json={"goal": "Something slow to research"}, headers=H).json()["id"]
    time.sleep(0.3)
    assert c.post(f"/api/team/runs/{rid}/cancel", headers=H).json() == {"ok": True}
    assert wait(c, rid)["status"] == "cancelled"

    def no_model(mid):
        raise RuntimeError("No LLM provider is configured.")
    monkeypatch.setattr(c.manager, "models", no_model)
    assert c.post("/api/team/runs", json={"goal": "Anything at all here"}, headers=H).status_code == 503
