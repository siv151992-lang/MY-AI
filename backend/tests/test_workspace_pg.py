"""End-to-end workspace API test: PostgreSQL, the real sandbox server, git, and a scripted model.

Needs TEST_DATABASE_URL (schema from db/init/01-schema.sh), like tests/test_memory_pg.py.
"""
import io
import json
import os
import sys
import threading
import time
import uuid
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to run Postgres integration tests")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
import server as sandbox_server  # noqa: E402

H = {"X-Requested-With": "aria"}


class ScriptedLLM:
    """Turn 1: write a module + test and run the tests. Turn 2: ask to delete a file. Then answer."""
    def __init__(self):
        self.calls = 0

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None, reasoning=None):
        self.calls += 1
        last = messages[-1]["content"]
        results = [p for p in last if p["type"] == "tool_result"]
        if not results:
            yield {"type": "tool_call", "id": "t1", "name": "write_files", "input": {"files": [
                {"path": "mathlib.py", "content": "def mul(a, b):\n    return a * b\n"},
                {"path": "test_mathlib.py", "content": "from mathlib import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n"}]}}
            yield {"type": "tool_call", "id": "t2", "name": "run_tests", "input": {}}
            yield {"type": "done", "stop_reason": "tool_use", "usage": {}}
        elif results[0]["tool_call_id"] == "t1":
            yield {"type": "tool_call", "id": "t3", "name": "delete_path", "input": {"path": "README.md"}}
            yield {"type": "done", "stop_reason": "tool_use", "usage": {}}
        else:
            yield {"type": "text", "text": "Added mathlib.mul with a passing test."}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}

    async def complete(self, *, model, system, messages, max_tokens=1024, **_):
        return "{}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sandbox_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("SANDBOX_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("SANDBOX_TOKEN", sandbox_server.TOKEN)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1000")
    from app.config import get_settings
    from app.llm import registry
    from app.llm.registry import ModelSpec
    get_settings.cache_clear()
    llm = ScriptedLLM()
    spec = ModelSpec(id="fake", label="Fake", provider="fake", model="f", vision=False, tools=True, max_tokens=500,
                     reasoning="none")
    monkeypatch.setattr(registry, "get_model", lambda *a, **k: spec)
    monkeypatch.setattr(registry, "utility_model", lambda: spec)
    monkeypatch.setattr(registry, "provider_for", lambda s: llm)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": f"w{uuid.uuid4().hex[:8]}@example.com", "name": "Dev",
                                              "password": "password1234"}, headers=H)
        assert r.status_code == 200, r.text
        yield c
    srv.shutdown()
    get_settings.cache_clear()


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_files_git_terminal_and_confirmations(client):
    ws = client.post("/api/workspaces", json={"name": "Demo", "description": "test"}, headers=H).json()
    wid = ws["id"]
    assert client.get(f"/api/workspaces/{wid}/file", params={"path": "README.md"}).json()["content"].startswith("# Demo")
    r = client.put(f"/api/workspaces/{wid}/file", json={"path": "src/app.py", "content": "print('hi')\n"}, headers=H)
    assert r.json()["created"]
    assert client.put(f"/api/workspaces/{wid}/file", json={"path": "../x", "content": ""}, headers=H).status_code == 422
    # optimistic concurrency: the editor's copy is stale
    assert client.put(f"/api/workspaces/{wid}/file", json={"path": "src/app.py", "content": "x", "expected": "old"},
                      headers=H).status_code == 409
    tree = client.get(f"/api/workspaces/{wid}/tree").json()
    assert tree["changes"] == {"src/app.py": "untracked"} or tree["changes"]["src/app.py"] in ("untracked", "added")
    st = client.get(f"/api/workspaces/{wid}/git/status").json()
    assert st["files"][0]["path"] == "src/app.py" and st["files"][0]["added"] == 1
    assert "+print('hi')" in client.get(f"/api/workspaces/{wid}/git/diff").json()["diff"]
    # terminal
    r = client.post(f"/api/workspaces/{wid}/exec", json={"command": "python3 src/app.py && touch made.txt"}, headers=H).json()
    assert r["exit_code"] == 0 and r["stdout"] == "hi\n" and r["written"] == ["made.txt"]
    # destructive actions need explicit confirmation
    assert client.post(f"/api/workspaces/{wid}/delete", json={"path": "made.txt"}, headers=H).status_code == 428
    assert client.post(f"/api/workspaces/{wid}/delete", json={"path": "made.txt", "confirm": True}, headers=H).json() == {"deleted": ["made.txt"]}
    c = client.post(f"/api/workspaces/{wid}/git/commit", json={"message": "Add app"}, headers=H).json()
    assert c["message"] == "Add app" and c["author"] == "Dev"
    assert [x["message"] for x in client.get(f"/api/workspaces/{wid}/git/log").json()] == ["Add app", "Initial commit"]
    assert client.post(f"/api/workspaces/{wid}/git/push", json={}, headers=H).status_code == 428
    assert client.post(f"/api/workspaces/{wid}/git/push", json={"confirm": True}, headers=H).status_code == 422  # no remote
    assert client.put(f"/api/workspaces/{wid}/git/remote", json={"url": "http://insecure/x.git"}, headers=H).status_code == 422
    br = client.post(f"/api/workspaces/{wid}/git/checkout", json={"name": "feature", "create": True}, headers=H).json()
    assert br["current"] == "feature"
    z = client.get(f"/api/workspaces/{wid}/export")
    assert sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == ["Demo/README.md", "Demo/src/app.py"]
    kinds = [a["kind"] for a in client.get(f"/api/workspaces/{wid}/activity").json()]
    assert {"create", "write", "command", "delete", "commit", "branch"} <= set(kinds)
    assert client.delete(f"/api/workspaces/{wid}", headers=H).status_code == 428
    assert client.delete(f"/api/workspaces/{wid}?confirm=true", headers=H).json() == {"ok": True}
    assert client.get(f"/api/workspaces/{wid}").status_code == 404


def test_import_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo-main/pkg/mod.py", "X = 1\n")
    r = client.post("/api/workspaces/import", params={"name": "Imported"},
                    files={"file": ("p.zip", buf.getvalue(), "application/zip")}, headers=H)
    wid = r.json()["id"]
    assert [e["path"] for e in client.get(f"/api/workspaces/{wid}/tree").json()["entries"]] == ["pkg", "pkg/mod.py"]
    assert client.get(f"/api/workspaces/{wid}/git/log").json()[0]["message"] == "Import project"


def test_ai_turn_edits_runs_tests_and_waits_for_delete_approval(client):
    wid = client.post("/api/workspaces", json={"name": "AI"}, headers=H).json()["id"]
    seen = {}

    def answer_from_ui():
        # TestClient buffers streamed responses, so act like a second browser request while the turn waits.
        deadline = time.time() + 30
        while time.time() < deadline:
            pending = client.get(f"/api/workspaces/{wid}/approvals").json()
            if pending:
                seen["pending"] = pending[0]
                seen["decision"] = client.post(f"/api/workspaces/{wid}/approvals/{pending[0]['id']}",
                                               json={"approved": False}, headers=H).json()
                return
            time.sleep(0.05)

    t = threading.Thread(target=answer_from_ui)
    t.start()
    resp = client.post(f"/api/workspaces/{wid}/chat", json={"content": "Add a mul function with a test",
                                                           "editor": {"path": "README.md"}}, headers=H)
    t.join()
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    assert seen["pending"]["kind"] == "delete" and seen["pending"]["details"]["paths"] == ["README.md"]
    assert seen["decision"]["status"] == "denied"
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and "checkpoint" in types
    end = next(e for e in events if e["type"] == "command_end")
    assert end["exit_code"] == 0 and "1 passed" in end["stdout"]
    assert "approval_decided" in types and types.index("approval_required") < types.index("approval_decided")
    done = next(e for e in events if e["type"] == "done")
    assert done["message"]["content"] == "Added mathlib.mul with a passing test."
    delete = next(t for t in done["message"]["meta"]["tools"] if t["name"] == "delete_path")
    assert not delete["is_error"] and "declined" in delete["output"], delete
    assert any(a["kind"] == "approval" and a["summary"].startswith("Denied")
               for a in client.get(f"/api/workspaces/{wid}/activity").json())
    cp = done["message"]["meta"]["checkpoint"]
    turn = next(e for e in events if e["type"] == "turn_changes")
    assert {f["path"] for f in turn["files"]} == {"mathlib.py", "test_mathlib.py"}
    # README survived the declined delete
    assert client.get(f"/api/workspaces/{wid}/file", params={"path": "README.md"}).status_code == 200
    # review the turn's diff, then undo it
    ch = client.get(f"/api/workspaces/{wid}/checkpoints/{cp}/changes").json()
    assert "+def mul(a, b):" in ch["diff"]
    assert client.post(f"/api/workspaces/{wid}/checkpoints/restore", json={"sha": cp}, headers=H).status_code == 428
    client.post(f"/api/workspaces/{wid}/checkpoints/restore", json={"sha": cp, "confirm": True}, headers=H)
    paths = [e["path"] for e in client.get(f"/api/workspaces/{wid}/tree").json()["entries"]]
    assert paths == ["README.md"]
    # the coding chat is kept with the workspace, not in the main chat list
    conv_id = events[0]["id"]
    assert client.get(f"/api/workspaces/{wid}").json()["conversations"][0]["id"] == conv_id
    assert all(c["id"] != conv_id for c in client.get("/api/conversations").json())
