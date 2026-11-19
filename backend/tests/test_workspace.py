"""Coding workspace: file safety, edits, git + checkpoints, sandbox commands, code search, approvals."""
import asyncio
import io
import os
import sys
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.agent.tools.base import ToolContext, ToolRegistry
from app.services.chat import merge_events
from app.workspace import git, index, runner
from app.workspace.approvals import ApprovalGate
from app.workspace.fs import Workspace, WorkspaceError, apply_edit, normalize
from app.workspace.service import extract_zip
from app.workspace.tools import Session, make_workspace_tools

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
os.environ.setdefault("SANDBOX_TOKEN", "test-token")
import server as sandbox_server  # noqa: E402

run = asyncio.run


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "ws" / "repo"
    run(git.init(root, ("Tester", "t@example.com")))
    return Workspace(root)


@pytest.fixture(scope="module")
def sandbox_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sandbox_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ---------- paths & edits ----------
@pytest.mark.parametrize("bad", ["../x", "/../etc/passwd", "a/../../b", ".git/config", "src/.git/hooks/pre-commit", "a\x00b"])
def test_normalize_rejects_escapes_and_git(bad):
    with pytest.raises(WorkspaceError):
        normalize(bad)


def test_normalize_cleans():
    assert normalize("./src/app.py") == "src/app.py" and normalize("") == ""
    assert normalize("/src/app.py") == "src/app.py"  # a leading slash means the project root, never the host


def test_symlink_escape_is_blocked(repo, tmp_path):
    (tmp_path / "secret.txt").write_text("s3cret")
    os.symlink(tmp_path, repo.root / "link")
    with pytest.raises(WorkspaceError):
        repo.read_text("link/secret.txt")
    assert "link" not in [e["path"] for e in repo.tree()]


def test_write_read_tree_and_delete_root_refused(repo):
    r = repo.write_text("src/app.py", "print('hi')\n")
    assert r["created"] and r["added"] == 1
    assert repo.read_text("src/app.py") == "print('hi')\n"
    assert {"path": "src", "type": "dir"} in repo.tree()
    with pytest.raises(WorkspaceError):
        repo.delete("")


def test_apply_edit_rules():
    text = "a = 1\nb = 2\na = 1\n"
    with pytest.raises(WorkspaceError, match="occurs 2 times"):
        apply_edit(text, "a = 1", "a = 3")
    assert apply_edit(text, "a = 1", "a = 3", replace_all=True) == ("a = 3\nb = 2\na = 3\n", 2)
    assert apply_edit(text, "b = 2", "b = 5")[0] == "a = 1\nb = 5\na = 1\n"
    with pytest.raises(WorkspaceError, match="different whitespace"):
        apply_edit("def f():\n    return 1\n", "def f():\n  return 1", "x")


# ---------- git ----------
def test_status_diff_commit_discard(repo):
    repo.write_text("README.md", "# Changed\n")
    repo.write_text("new.py", "x = 1\n")
    st = run(git.status(repo.root))
    assert st["branch"] == "main" and {f["path"] for f in st["files"]} == {"README.md", "new.py"}
    d = run(git.diff(repo.root))
    assert "+# Changed" in d and "new.py" in d and "+x = 1" in d
    assert run(git.numstat(repo.root))["new.py"] == (1, 0)
    c = run(git.commit_all(repo.root, "Add new.py", paths=["new.py"], author=("Ann", "ann@example.com")))
    assert c["message"] == "Add new.py" and c["author"] == "Ann"
    assert [f["path"] for f in run(git.status(repo.root))["files"]] == ["README.md"]
    repo.write_text("scratch.txt", "tmp")
    assert set(run(git.discard(repo.root, ["README.md", "scratch.txt"]))) == {"README.md", "scratch.txt"}
    assert run(git.status(repo.root))["clean"] and not repo.exists("scratch.txt")
    assert run(git.commit_all(repo.root, "nothing")) is None


def test_checkpoint_restore_roundtrip(repo):
    repo.write_text("keep.py", "v1\n")
    run(git.commit_all(repo.root, "base"))
    repo.write_text("wip.py", "user's uncommitted work\n")
    cp = run(git.checkpoint(repo.root, "Before AI turn"))
    # the "AI" modifies, deletes and creates files
    repo.write_text("keep.py", "v2\n")
    repo.delete("wip.py")
    repo.write_text("generated.py", "new\n")
    changes = {c["path"]: c["status"] for c in run(git.changed_since(repo.root, cp))}
    assert changes == {"keep.py": "modified", "wip.py": "deleted", "generated.py": "added"}
    run(git.restore_checkpoint(repo.root, cp))
    assert repo.read_text("keep.py") == "v1\n" and repo.read_text("wip.py") == "user's uncommitted work\n"
    assert not repo.exists("generated.py")
    # index/branch untouched, and the restore itself made a checkpoint
    assert run(git.log(repo.root, 1))[0]["message"] == "base"
    assert len(run(git.list_checkpoints(repo.root))) == 2


def test_branches_and_validation(repo):
    run(git.create_branch(repo.root, "feature/x"))
    assert run(git.branches(repo.root))["current"] == "feature/x"
    repo.write_text("a.txt", "dirty")
    with pytest.raises(git.GitError, match="Commit or discard"):
        run(git.checkout(repo.root, "main"))
    for bad in ("-rf", "a..b", "x/", "has space"):
        with pytest.raises(git.GitError):
            git.check_branch(bad)


def test_remote_url_rules_and_push_without_remote(repo):
    for bad in ("http://example.com/r.git", "https://user:pw@github.com/r.git", "ssh://git@github.com/r", "file:///tmp/x"):
        with pytest.raises(git.GitError):
            git.check_remote_url(bad)
    with pytest.raises(git.GitError, match="No remote"):
        run(git.push(repo.root))


def test_repo_config_cannot_run_hooks(repo, tmp_path):
    marker = tmp_path / "pwned"
    hook = repo.root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    repo.write_text("x.txt", "1")
    run(git.commit_all(repo.root, "with hook present"))
    assert not marker.exists()


# ---------- sandbox commands ----------
def test_execute_syncs_writes_and_reports_deletes(repo, sandbox_url):
    repo.write_text("calc.py", "def add(a, b):\n    return a + b\n")
    repo.write_text("test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    repo.write_text("old.log", "x")
    res = run(runner.execute(repo, "python3 -c \"open('out.txt','w').write('done')\" && rm old.log && echo ok",
                             sandbox_url=sandbox_url, token="test-token"))
    assert res.exit_code == 0 and "ok" in res.stdout
    assert res.written == {"out.txt": b"done"} and res.deleted == ["old.log"]
    assert runner.apply_writes(repo, res) == ["out.txt"] and repo.read_text("out.txt") == "done"
    assert repo.exists("old.log")  # deletions are not applied until approved
    assert runner.apply_deletes(repo, res.deleted) == ["old.log"] and not repo.exists("old.log")
    assert runner.detect_test_command(repo) == "python -m pytest -q"


def test_execute_reports_failures_and_bad_cwd(repo, sandbox_url):
    res = run(runner.execute(repo, "echo boom >&2; exit 3", sandbox_url=sandbox_url, token="test-token"))
    assert res.exit_code == 3 and "boom" in res.stderr
    with pytest.raises(WorkspaceError):
        run(runner.execute(repo, "ls", cwd="nope", sandbox_url=sandbox_url, token="test-token"))
    with pytest.raises(WorkspaceError, match="No sandbox"):
        run(runner.execute(repo, "ls", sandbox_url=None, token=None))


# ---------- code understanding ----------
def test_symbols_and_repo_map(repo):
    repo.write_text("svc/users.py", "class UserService:\n    def get_user(self, uid): ...\n    def _x(self): ...\n\n"
                                    "async def login(name, password):\n    pass\n")
    repo.write_text("web/api.ts", "export async function fetchUser(id: string) {}\nexport const save = async (u) => u\n"
                                  "export interface User { id: string }\nclass Store {}\n")
    m = index.repo_map(repo)
    assert "svc/users.py: class UserService [get_user, _x], def login(name, password)" in m
    assert "web/api.ts: fetchUser, Store, save, User" in m


def test_grep_and_relevance(repo):
    repo.write_text("auth/session.py", "def authenticate_user(token):\n    return verify_jwt(token)\n")
    repo.write_text("billing/invoice.py", "def total(items):\n    return sum(items)\n")
    hits = index.grep(repo, "verify_jwt")
    assert hits == [{"path": "auth/session.py", "line": 2, "text": "return verify_jwt(token)"}]
    assert index.grep(repo, r"def \w+\(items", regex=True)[0]["path"] == "billing/invoice.py"
    with pytest.raises(WorkspaceError):
        index.grep(repo, "(", regex=True)
    assert index.relevant_files(repo, "where are users authenticated?")[0]["path"] == "auth/session.py"


# ---------- import ----------
def test_extract_zip_is_safe_and_strips_top_folder(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("proj-main/src/a.py", "a")
        z.writestr("proj-main/.git/config", "[core]")
        z.writestr("proj-main/node_modules/x/index.js", "x")
        z.writestr("proj-main/../../evil.txt", "evil")
    dest = tmp_path / "out"
    dest.mkdir()
    assert extract_zip(buf.getvalue(), dest, limit_mb=5) == 1
    assert (dest / "src/a.py").read_text() == "a"
    assert not (dest / ".git").exists() and not (tmp_path / "evil.txt").exists()
    with pytest.raises(WorkspaceError):
        extract_zip(b"not a zip", dest, limit_mb=5)


# ---------- approvals & agent tools ----------
def _session(repo, gate, events, sandbox_url=None):
    async def emit(ev):
        events.append(ev)
        if ev["type"] == "approval_required":  # simulate the user clicking in the UI
            asyncio.get_running_loop().call_soon(gate.decide, "u1", ev["approval"]["id"], gate.answer)

    async def log(actor, kind, summary, **detail):  # same signature as the real activity logger
        assert "kind" not in detail and "actor" not in detail
    return Session(workspace_id="w1", user={"id": "u1", "name": "Ann", "email": "a@x"}, fs=repo, gate=gate,
                   lock=asyncio.Lock(), emit=emit, log=log, sandbox_url=sandbox_url, sandbox_token="test-token")


def _call(reg, name, args):
    return run(reg.execute(name, args, ToolContext(user_id="u1", conversation_id="c1")))


def test_delete_needs_approval(repo):
    gate = ApprovalGate(timeout_s=5)
    events = []
    reg = ToolRegistry(make_workspace_tools(_session(repo, gate, events)))
    repo.write_text("a.txt", "x")
    gate.answer = False
    out, err = _call(reg, "delete_path", {"path": "a.txt"})
    assert not err and "declined" in out and repo.exists("a.txt")
    gate.answer = True
    out, err = _call(reg, "delete_path", {"path": "a.txt"})
    assert "Deleted a.txt" in out and not repo.exists("a.txt")
    kinds = [e["type"] for e in events]
    assert kinds.count("approval_required") == 2 and kinds.count("approval_decided") == 2
    assert events[-1] == {"type": "workspace_change", "paths": ["a.txt"], "action": "delete"}


def test_edit_write_and_commit_tools(repo):
    gate = ApprovalGate(timeout_s=5)
    gate.answer = True
    events = []
    reg = ToolRegistry(make_workspace_tools(_session(repo, gate, events)))
    out, err = _call(reg, "write_files", {"files": [{"path": "app/main.py", "content": "def hello():\n    return 'hi'\n"},
                                                    {"path": "app/__init__.py", "content": ""}]})
    assert not err and "created app/main.py" in out
    out, err = _call(reg, "edit_file", {"path": "app/main.py", "old_text": "'hi'", "new_text": "'hello'"})
    assert not err and repo.read_text("app/main.py").endswith("'hello'\n")
    out, err = _call(reg, "edit_file", {"path": "app/main.py", "old_text": "nope", "new_text": "x"})
    assert err and "not found" in out
    out, err = _call(reg, "read_file", {"path": "app/main.py", "start_line": 2})
    assert "    2      return 'hello'" in out
    out, err = _call(reg, "git_commit", {"message": "Add app"})
    assert "Committed" in out and run(git.log(repo.root, 1))[0]["author"] == "Ann"
    commit_req = next(e for e in events if e["type"] == "approval_required")["approval"]
    assert commit_req["kind"] == "commit" and {f["path"] for f in commit_req["details"]["files"]} == {"app/main.py", "app/__init__.py"}
    out, err = _call(reg, "write_file", {"path": ".git/config", "content": "x"})
    assert err and ".git" in out


def test_run_command_deletions_are_gated(repo, sandbox_url):
    gate = ApprovalGate(timeout_s=5)
    gate.answer = False
    events = []
    reg = ToolRegistry(make_workspace_tools(_session(repo, gate, events, sandbox_url)))
    repo.write_text("data.csv", "1,2\n")
    out, err = _call(reg, "run_command", {"command": "rm data.csv && echo made > made.txt"})
    assert "declined" in out and repo.exists("data.csv") and repo.read_text("made.txt") == "made\n"
    assert any(e["type"] == "command_end" and e["exit_code"] == 0 for e in events)
    repo.write_text("test_ok.py", "def test_ok():\n    assert True\n")
    out, err = _call(reg, "run_tests", {})
    assert "exit_code: 0" in out and "1 passed" in out


def test_gate_timeout_and_wrong_user():
    gate = ApprovalGate(timeout_s=0.05)
    seen = []

    async def go():
        ok = await gate.request(workspace_id="w", user_id="u1", kind="delete", summary="x", details={},
                                emit=lambda ev: seen.append(ev))
        return ok
    assert run(go()) is False and seen[-1]["status"] == "expired"
    assert gate.decide("someone-else", "nope", True) is None


def test_merge_events_interleaves_side_events():
    async def go():
        q = asyncio.Queue()

        async def agent():
            yield {"type": "a1"}
            await q.put({"type": "side"})
            await asyncio.sleep(0.01)
            yield {"type": "a2"}
        return [e["type"] async for e in merge_events(agent(), q)]
    assert run(go()) == ["a1", "side", "a2"]
