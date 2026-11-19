"""Workspace lifecycle (create / clone / import / export / delete) and the coding chat turn."""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from ..agent.tools.base import ToolRegistry
from ..config import get_settings
from . import git, index, store
from .approvals import gate
from .fs import IGNORED_DIRS, Workspace, WorkspaceError, normalize
from .tools import Session, make_workspace_tools

log = logging.getLogger("aria.workspace")
_locks: dict[str, asyncio.Lock] = {}

CODING_PROMPT = """You are working as a senior software engineer inside the user's coding workspace: a real \
project (a git repository) that you can read, edit, run and test with your tools.

How you work:
- Understand before changing: use repo_map, search_code and read_file to find the relevant code. Never guess file \
contents or APIs that exist in the project; read them.
- Make focused changes. Use edit_file for existing files (copy old_text exactly from read_file output, without the \
line-number prefix) and write_file / write_files for new files. When asked to build an application, create every \
file needed to run it (source, config, dependency manifest, README, tests).
- Verify: run the tests or the program with run_tests / run_command after changing code, read the errors, fix \
them, and re-run until it works or you have a clear blocker. The sandbox has no network: dependencies that are \
not already installed (python stdlib, pytest, numpy, pandas; node built-ins) cannot be installed, so say so and \
write code that can be tested offline where possible.
- Debugging: reproduce first, find the root cause from the actual error output, fix the cause rather than the \
symptom, and confirm the fix by re-running.
- Destructive actions (delete_path, git_commit, and commands that delete files) ask the user for approval. If they \
decline, respect it and do not work around it. You cannot push; tell the user to push from the Source Control panel.
- When explaining code, refer to concrete files and line numbers. When generating documentation, write it into the \
project (README.md, docs/…, docstrings) unless asked only to explain.
- Finish with a short summary: what you changed (files), how you verified it (commands and results), and anything \
left for the user. The user sees a diff of every change, so do not paste whole files back into the chat."""


def root(ws_id: str) -> Path:
    return get_settings().workspace_root / ws_id / "repo"


def fs(ws_id: str) -> Workspace:
    r = root(ws_id)
    if not r.is_dir():
        raise WorkspaceError("The workspace files are missing on the server")
    return Workspace(r)


def lock(ws_id: str) -> asyncio.Lock:
    return _locks.setdefault(ws_id, asyncio.Lock())


def author(user: dict) -> tuple[str, str]:
    return user.get("name") or "Aria user", user.get("email") or "user@localhost"


# ---------- lifecycle ----------
async def create(user: dict, name: str, description: str = "", *, clone_url: str | None = None,
                 archive: bytes | None = None) -> dict:
    source = "clone" if clone_url else "import" if archive else "blank"
    ws = await store.create(user["id"], name, description, source)
    r = root(ws["id"])
    try:
        if clone_url:
            await git.clone(clone_url, r)
        else:
            r.mkdir(parents=True, exist_ok=True)
            if archive:
                await asyncio.to_thread(extract_zip, archive, r)
            else:
                (r / "README.md").write_text(f"# {name}\n\n{description}\n".rstrip() + "\n")
            await git.init(r, author(user), "Import project" if archive else "Initial commit")
    except Exception:
        await store.delete(user["id"], ws["id"])
        shutil.rmtree(r.parent, ignore_errors=True)
        raise
    await store.log(ws["id"], user["id"], "user", "create", f"Created workspace ({source})")
    return ws


async def delete(user: dict, ws_id: str) -> bool:
    if not await store.delete(user["id"], ws_id):
        return False
    gate().cancel_workspace(ws_id)
    await asyncio.to_thread(shutil.rmtree, root(ws_id).parent, True)
    _locks.pop(ws_id, None)
    return True


def extract_zip(data: bytes, dest: Path, limit_mb: int | None = None) -> int:
    """Safely extract an uploaded .zip: no absolute paths, no '..', no symlinks, no .git, size-capped.
    A single top-level folder (as in GitHub downloads) is stripped."""
    limit = (limit_mb or get_settings().workspace_import_max_mb) * 1024 * 1024
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise WorkspaceError("Not a valid .zip file")
    members = [m for m in zf.infolist() if not m.is_dir()]
    if len(members) > 10_000:
        raise WorkspaceError("Archive has too many files")
    if sum(m.file_size for m in members) > limit:
        raise WorkspaceError(f"Archive is larger than {limit // 1024 // 1024} MB uncompressed")
    names = [PurePosixPath(m.filename.replace("\\", "/")) for m in members]
    tops = {n.parts[0] for n in names if n.parts}
    strip = len(tops) == 1 and all(len(n.parts) > 1 for n in names)
    count = 0
    for m, n in zip(members, names):
        if (m.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
            continue
        parts = n.parts[1:] if strip else n.parts
        if not parts or any(p in IGNORED_DIRS for p in parts[:-1]) or parts[0] == "__MACOSX":
            continue
        try:
            rel = normalize("/".join(parts))
        except WorkspaceError:
            continue  # skips .git, absolute and parent-relative paths
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(m) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        count += 1
    return count


def export_zip(ws: Workspace, name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in ws.tree():
            if e["type"] == "file":
                zf.write(ws.root / e["path"], f"{name}/{e['path']}")
    return buf.getvalue()


# ---------- coding chat ----------
def _context_sections(ws: Workspace, ws_row: dict, status: dict, editor: dict | None) -> list[str]:
    files = "\n".join(f"  {f['status']:<9} {f['path']}" for f in status["files"][:40]) or "  (no uncommitted changes)"
    parts = [CODING_PROMPT,
             f"Project: {ws_row['name']}" + (f" — {ws_row['description']}" if ws_row.get("description") else "") +
             f"\nGit branch: {status['branch']}\nUncommitted changes:\n{files}",
             "Project map (files and their top-level symbols):\n" + index.repo_map(ws, max_chars=8000)]
    if editor and editor.get("path"):
        sec = f"The user has `{editor['path']}` open in the editor"
        if editor.get("selection"):
            sec += f" with this selected (lines {editor.get('start_line', '?')}-{editor.get('end_line', '?')}):\n" \
                   f"```\n{str(editor['selection'])[:6000]}\n```"
        else:
            sec += "."
        parts.append(sec + " \"This file\" / \"this code\" in their message refers to it.")
    return parts


async def respond(*, user: dict, ws_row: dict, conv: dict, text: str, model_id: str | None,
                  editor: dict | None, regenerate: bool = False) -> AsyncIterator[dict]:
    from ..agent.tools.web import make_web_tools
    from ..services import chat
    s = get_settings()
    w = fs(ws_row["id"])
    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict) -> None:
        q.put_nowait(ev)

    async def log_(actor: str, kind: str, summary: str, **detail) -> None:
        try:
            await store.log(ws_row["id"], user["id"], actor, kind, summary, **detail)
        except Exception:
            log.exception("activity log failed")

    session = Session(workspace_id=ws_row["id"], user=user, fs=w, gate=gate(), lock=lock(ws_row["id"]), emit=emit,
                      log=log_, sandbox_url=s.sandbox_url, sandbox_token=s.sandbox_token)
    async with session.lock:
        cp = await git.checkpoint(w.root, f"Before: {text[:80] or 'regenerate'}")
        status = await git.status(w.root)
    yield {"type": "checkpoint", "sha": cp}
    tools = ToolRegistry(make_workspace_tools(session) + make_web_tools(s.tavily_api_key, s.brave_api_key))
    async for ev in chat.respond(user=user, conv=conv, text=text, file_ids=[], model_id=model_id, agent_mode=False,
                                 regenerate=regenerate, tools=tools, side_events=q,
                                 extra_system=_context_sections(w, ws_row, status, editor),
                                 max_steps=s.workspace_max_agent_steps, extra_meta={"checkpoint": cp}, feature="workspace"):
        yield ev
    try:
        changes = await git.changed_since(w.root, cp)
    except Exception:
        log.exception("could not compute turn changes")
        changes = []
    if changes:
        yield {"type": "turn_changes", "checkpoint": cp, "files": changes}
    await store.touch(ws_row["id"])
