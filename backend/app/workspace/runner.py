"""Run commands in the sandbox against a copy of the project, then apply the files they changed.

The project is shipped to the sandbox as a tarball (minus .git and dependency/build folders), the
command runs there with no network and resource limits, and the sandbox returns the files that were
created, changed or deleted. Writes are applied directly (git and checkpoints keep the old versions);
deletions are destructive, so when the AI ran the command they are only applied after approval.
"""
from __future__ import annotations

import base64
import io
import os
import tarfile
from dataclasses import dataclass, field

from .fs import IGNORED_DIRS, Workspace, WorkspaceError, normalize

MAX_PROJECT_BYTES = 40 * 1024 * 1024


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    written: dict[str, bytes] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self, limit: int = 12_000) -> str:
        parts = [f"exit_code: {self.exit_code}" + ("  (timed out)" if self.timed_out else "")]
        if self.stdout:
            parts.append("stdout:\n" + self.stdout[-limit:])
        if self.stderr:
            parts.append("stderr:\n" + self.stderr[-limit:])
        if not self.stdout and not self.stderr:
            parts.append("(no output)")
        return "\n".join(parts)


def pack(ws: Workspace) -> str:
    """tar.gz (base64) of the project without .git and heavy generated folders."""
    buf = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tf:
        for d, dirs, files in os.walk(ws.root):
            dirs[:] = [x for x in dirs if x not in IGNORED_DIRS and not os.path.islink(os.path.join(d, x))]
            for f in files:
                p = os.path.join(d, f)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                total += os.path.getsize(p)
                if total > MAX_PROJECT_BYTES:
                    raise WorkspaceError("The project is too large to run in the sandbox (40 MB limit)")
                tf.add(p, arcname=os.path.relpath(p, ws.root), recursive=False)
    return base64.b64encode(buf.getvalue()).decode()


async def execute(ws: Workspace, command: str, *, cwd: str = "", timeout: int = 60,
                  sandbox_url: str | None, token: str | None) -> ExecResult:
    import asyncio

    import httpx
    if not sandbox_url:
        raise WorkspaceError("No sandbox is configured (SANDBOX_URL), so commands cannot run")
    archive = await asyncio.to_thread(pack, ws)
    headers = {"authorization": f"Bearer {token}"} if token else {}
    timeout = max(1, min(int(timeout), 300))
    async with httpx.AsyncClient(timeout=timeout + 60) as c:
        r = await c.post(f"{sandbox_url.rstrip('/')}/exec", headers=headers,
                         json={"archive": archive, "command": command, "timeout": timeout, "cwd": normalize(cwd)})
    if r.status_code == 400:
        raise WorkspaceError(r.json().get("error", "Sandbox rejected the command"))
    r.raise_for_status()
    data = r.json()
    ch = data.get("changes") or {}
    written = {}
    for path, b64 in (ch.get("written") or {}).items():
        try:
            written[normalize(path)] = base64.b64decode(b64)
        except (WorkspaceError, ValueError):
            continue  # never apply anything that targets .git or escapes the project
    deleted = []
    for p in ch.get("deleted") or []:
        try:
            deleted.append(normalize(p))
        except WorkspaceError:
            continue
    return ExecResult(int(data.get("exit_code", -1)), data.get("stdout", ""), data.get("stderr", ""),
                      bool(data.get("timed_out")), written, deleted, list(ch.get("skipped") or []))


def apply_writes(ws: Workspace, res: ExecResult) -> list[str]:
    for path, data in res.written.items():
        ws.write_bytes(path, data)
    return sorted(res.written)


def apply_deletes(ws: Workspace, paths: list[str]) -> list[str]:
    done = []
    for p in paths:
        try:
            if ws.resolve(p).is_file():
                ws.delete(p)
                done.append(p)
        except WorkspaceError:
            continue
    return done


TEST_COMMANDS = [
    ("pytest.ini", "python -m pytest -q"), ("pyproject.toml", "python -m pytest -q"), ("setup.cfg", "python -m pytest -q"),
    ("conftest.py", "python -m pytest -q"), ("package.json", "npm test --silent"), ("go.mod", "go test ./..."),
    ("Cargo.toml", "cargo test"), ("Makefile", "make test"),
]


def detect_test_command(ws: Workspace) -> str | None:
    files = set(ws.files())
    for marker, cmd in TEST_COMMANDS:
        if marker in files:
            if marker == "package.json":
                import json
                try:
                    scripts = json.loads(ws.read_text("package.json")).get("scripts", {})
                except (ValueError, WorkspaceError):
                    scripts = {}
                if "test" not in scripts:
                    continue
            return cmd
    if any(f.endswith(".py") and (os.path.basename(f).startswith("test_") or f.endswith("_test.py")) for f in files):
        return "python -m pytest -q"
    if any(f.endswith((".test.js", ".test.mjs", ".test.ts")) for f in files):
        return "node --test"
    return None
