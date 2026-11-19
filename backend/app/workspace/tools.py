"""The coding agent's tools. Non-destructive edits apply immediately (every turn starts from a
checkpoint, so they can be undone); deletions, discarding changes and commits wait for approval."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..agent.tools.base import Tool, ToolContext
from . import git, index, runner
from .approvals import ApprovalGate
from .fs import Workspace, WorkspaceError, normalize

MAX_READ_LINES = 1200
MAX_READ_CHARS = 100_000


@dataclass
class Session:
    """Everything a tool call needs: the project, who is acting, and how to reach the client."""
    workspace_id: str
    user: dict
    fs: Workspace
    gate: ApprovalGate
    lock: asyncio.Lock
    emit: Callable[[dict], Awaitable[None]]
    log: Callable[..., Awaitable[None]]
    sandbox_url: str | None = None
    sandbox_token: str | None = None
    changed: set[str] = field(default_factory=set)

    async def changed_files(self, paths: list[str], action: str) -> None:
        self.changed.update(paths)
        await self.emit({"type": "workspace_change", "paths": paths, "action": action})

    async def approve(self, kind: str, summary: str, **details) -> bool:
        ok = await self.gate.request(workspace_id=self.workspace_id, user_id=self.user["id"], kind=kind,
                                     summary=summary, details=details, emit=self.emit)
        await self.log("assistant", "approval", f"{'Approved' if ok else 'Denied'}: {summary}", approval_kind=kind)
        return ok

    @property
    def author(self) -> tuple[str, str]:
        return self.user.get("name") or "Aria user", self.user.get("email") or "user@localhost"


def _fmt_tree(entries: list[dict], limit: int) -> str:
    lines = [f"{e['path']}/" if e["type"] == "dir" else f"{e['path']}  ({e.get('size', 0)} B)" for e in entries[:limit]]
    if len(entries) > limit:
        lines.append(f"… {len(entries) - limit} more entries (narrow the path)")
    return "\n".join(lines) or "(empty)"


def make_workspace_tools(s: Session) -> list[Tool]:
    fs = s.fs

    # ---------- understanding ----------
    async def list_files(args: dict, ctx: ToolContext) -> str:
        base = normalize(args.get("path", ""))
        entries = [e for e in fs.tree() if not base or e["path"] == base or e["path"].startswith(base + "/")]
        return _fmt_tree(entries, int(args.get("max_entries", 400)))

    async def read_file(args: dict, ctx: ToolContext) -> str:
        path = normalize(args["path"])
        lines = fs.read_text(path).splitlines()
        start = max(1, int(args.get("start_line") or 1))
        end = min(len(lines), int(args.get("end_line") or start + MAX_READ_LINES - 1))
        out, size = [], 0
        for n in range(start, end + 1):
            ln = f"{n:>5}  {lines[n - 1]}"
            size += len(ln)
            if size > MAX_READ_CHARS:
                end = n - 1
                break
            out.append(ln)
        head = f"{path} — lines {start}-{end} of {len(lines)}"
        tail = f"\n… more below: read_file with start_line={end + 1}" if end < len(lines) else ""
        return head + "\n" + "\n".join(out) + tail

    async def search_code(args: dict, ctx: ToolContext) -> str:
        q = str(args["query"])
        mode = args.get("mode", "text")
        if mode == "relevant":
            hits = index.relevant_files(fs, q, k=10)
            return "\n\n".join(f"{h['path']} (score {h['score']})\n" + "\n".join("  " + m for m in h["matches"])
                               for h in hits) or "No relevant files found."
        hits = index.grep(fs, q, regex=mode == "regex", glob=args.get("glob"))
        return "\n".join(f"{h['path']}:{h['line']}: {h['text']}" for h in hits) or "No matches."

    async def repo_map(args: dict, ctx: ToolContext) -> str:
        return index.repo_map(fs, max_chars=20_000)

    # ---------- editing (non-destructive) ----------
    async def write_file(args: dict, ctx: ToolContext) -> str:
        async with s.lock:
            r = fs.write_text(args["path"], str(args["content"]))
        await s.changed_files([r["path"]], "write")
        await s.log("assistant", "write", f"{'Created' if r['created'] else 'Rewrote'} {r['path']}", **r)
        return f"{'Created' if r['created'] else 'Updated'} {r['path']} (+{r['added']} -{r['removed']})"

    async def write_files(args: dict, ctx: ToolContext) -> str:
        files = args.get("files") or []
        if not isinstance(files, list) or not files or len(files) > 60:
            raise WorkspaceError("files must be a list of 1-60 {path, content} objects")
        results = []
        async with s.lock:
            for f in files:
                results.append(fs.write_text(f["path"], str(f.get("content", ""))))
        paths = [r["path"] for r in results]
        await s.changed_files(paths, "write")
        await s.log("assistant", "write", f"Wrote {len(paths)} files", paths=paths)
        return "\n".join(f"{'created' if r['created'] else 'updated'} {r['path']} (+{r['added']} -{r['removed']})"
                         for r in results)

    async def edit_file(args: dict, ctx: ToolContext) -> str:
        from .fs import apply_edit, line_delta
        path = normalize(args["path"])
        async with s.lock:
            before = fs.read_text(path)
            after, n = apply_edit(before, str(args["old_text"]), str(args["new_text"]), bool(args.get("replace_all")))
            fs.write_text(path, after)
        d = line_delta(before, after)
        await s.changed_files([path], "edit")
        await s.log("assistant", "edit", f"Edited {path}", path=path, **d)
        return f"Edited {path}: {n} replacement(s) (+{d['added']} -{d['removed']})"

    async def move_path(args: dict, ctx: ToolContext) -> str:
        src, dst = normalize(args["from"]), normalize(args["to"])
        async with s.lock:
            fs.move(src, dst)
        await s.changed_files([src, dst], "move")
        await s.log("assistant", "move", f"Moved {src} → {dst}")
        return f"Moved {src} to {dst}"

    # ---------- destructive (approval) ----------
    async def delete_path(args: dict, ctx: ToolContext) -> str:
        path = normalize(args["path"])
        target = fs.resolve(path, must_exist=True)
        files = [e["path"] for e in fs.tree(include_ignored=True)
                 if e["type"] == "file" and (e["path"] == path or e["path"].startswith(path + "/"))] \
            if target.is_dir() else [path]
        if not await s.approve("delete", f"Delete {path}" + (f" ({len(files)} files)" if target.is_dir() else ""),
                               paths=files[:200], reason=str(args.get("reason", ""))[:300]):
            return f"The user declined deleting {path}. Do not try to delete it another way; continue without it."
        async with s.lock:
            removed = fs.delete(path)
        await s.changed_files(removed or [path], "delete")
        await s.log("assistant", "delete", f"Deleted {path}", paths=removed[:200])
        return f"Deleted {path} ({len(removed)} file(s))"

    async def git_commit(args: dict, ctx: ToolContext) -> str:
        msg = str(args["message"]).strip()
        paths = [normalize(p) for p in args.get("paths") or []]
        stats = await git.numstat(fs.root)
        files = [{"path": p, "added": a, "removed": d} for p, (a, d) in stats.items()
                 if not paths or p in paths or any(p.startswith(x + "/") for x in paths)]
        if not files:
            return "Nothing to commit."
        if not await s.approve("commit", f"Commit {len(files)} file(s): {msg.splitlines()[0][:100]}",
                               message=msg, files=files[:200]):
            return "The user declined the commit."
        async with s.lock:
            c = await git.commit_all(fs.root, msg, paths=paths or None, author=s.author)
        if not c:
            return "Nothing to commit."
        await s.emit({"type": "workspace_change", "paths": [], "action": "commit"})
        await s.log("assistant", "commit", f"Committed {c['short']}: {c['message']}", sha=c["sha"])
        return f"Committed {c['short']}: {c['message']}"

    # ---------- running ----------
    async def _run(command: str, cwd: str, timeout: int) -> str:
        await s.emit({"type": "command_start", "command": command, "cwd": cwd})
        res = await runner.execute(fs, command, cwd=cwd, timeout=timeout, sandbox_url=s.sandbox_url,
                                   token=s.sandbox_token)
        async with s.lock:
            written = runner.apply_writes(fs, res)
        notes = []
        if written:
            await s.changed_files(written, "command")
            notes.append(f"files written by the command: {', '.join(written[:30])}")
        if res.deleted:
            ok = await s.approve("command_deletes", f"`{command[:80]}` deleted {len(res.deleted)} file(s)",
                                 command=command, paths=res.deleted[:200])
            if ok:
                async with s.lock:
                    gone = runner.apply_deletes(fs, res.deleted)
                await s.changed_files(gone, "delete")
                notes.append(f"files deleted (approved): {', '.join(gone[:30])}")
            else:
                notes.append(f"the command deleted {len(res.deleted)} file(s) but the user declined, so they were kept")
        if res.skipped:
            notes.append(f"not synced (too large): {', '.join(res.skipped[:10])}")
        await s.emit({"type": "command_end", "command": command, "exit_code": res.exit_code,
                      "stdout": res.stdout[-8000:], "stderr": res.stderr[-8000:], "timed_out": res.timed_out})
        await s.log("assistant", "command", f"$ {command[:200]} → exit {res.exit_code}", exit_code=res.exit_code)
        return res.summary() + ("\n\n" + "\n".join(notes) if notes else "")

    async def run_command(args: dict, ctx: ToolContext) -> str:
        return await _run(str(args["command"]), str(args.get("cwd") or ""), int(args.get("timeout") or 60))

    async def run_tests(args: dict, ctx: ToolContext) -> str:
        cmd = args.get("command") or runner.detect_test_command(fs)
        if not cmd:
            return "No test setup detected. Pass an explicit command (e.g. 'python -m pytest -q' or 'node --test')."
        return await _run(str(cmd), str(args.get("cwd") or ""), int(args.get("timeout") or 180))

    # ---------- git (read) ----------
    async def git_status(args: dict, ctx: ToolContext) -> str:
        st = await git.status(fs.root)
        files = "\n".join(f"  {f['status']:<9} {f['path']}" for f in st["files"]) or "  (clean)"
        return f"branch: {st['branch']}" + (f" (ahead {st['ahead']}, behind {st['behind']})" if st["ahead"] or st["behind"] else "") + "\n" + files

    async def git_diff(args: dict, ctx: ToolContext) -> str:
        d = await git.diff(fs.root, args.get("path"))
        return d[:40_000] or "No changes."

    async def git_log(args: dict, ctx: ToolContext) -> str:
        commits = await git.log(fs.root, int(args.get("limit") or 15), args.get("path"))
        return "\n".join(f"{c['short']} {c['date'][:10]} {c['author']}: {c['message']}" for c in commits) or "No commits."

    approval_timeout = s.gate.timeout_s + 60
    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    path = {"type": "string", "description": "Path relative to the project root"}
    return [
        Tool("list_files", "List files and folders in the project (or under a sub-path).",
             obj({"path": path, "max_entries": {"type": "integer"}}, []), list_files, timeout=30),
        Tool("read_file", "Read a text file with line numbers. Large files: read in ranges.",
             obj({"path": path, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
             read_file, timeout=30),
        Tool("search_code", "Search the codebase. mode 'text' (literal, default) or 'regex' returns matching lines; "
                            "mode 'relevant' ranks files for a natural-language question (e.g. 'where are users "
                            "authenticated'). Optional glob filters paths, e.g. 'src/**/*.ts'.",
             obj({"query": {"type": "string"}, "mode": {"type": "string", "enum": ["text", "regex", "relevant"]},
                  "glob": {"type": "string"}}, ["query"]), search_code, timeout=60),
        Tool("repo_map", "Overview of the whole project: every file with its top-level functions and classes.",
             obj({}, []), repo_map, timeout=60),
        Tool("write_file", "Create a file or replace its entire content. Prefer edit_file for changes to existing files.",
             obj({"path": path, "content": {"type": "string"}}, ["path", "content"]), write_file, timeout=30),
        Tool("write_files", "Create or replace several files at once, e.g. to scaffold a complete application.",
             obj({"files": {"type": "array", "items": obj({"path": path, "content": {"type": "string"}},
                                                           ["path", "content"])}}, ["files"]), write_files, timeout=60),
        Tool("edit_file", "Replace an exact snippet in a file. old_text must match the file exactly (including "
                          "indentation) and be unique unless replace_all is true. Read the file first.",
             obj({"path": path, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                  "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]), edit_file, timeout=30),
        Tool("move_path", "Move or rename a file or folder (fails if the destination exists).",
             obj({"from": path, "to": path}, ["from", "to"]), move_path, timeout=30),
        Tool("delete_path", "Delete a file or folder. Asks the user for approval first.",
             obj({"path": path, "reason": {"type": "string"}}, ["path"]), delete_path, timeout=approval_timeout,
             requires_approval=True),
        Tool("run_command", "Run a shell command in an isolated sandbox against a copy of the project (no network; "
                            "python3, pytest, node, npm (offline), git, make available). Files the command writes "
                            "are synced back; deletions need the user's approval. Each call starts fresh: no state "
                            "carries over except files.",
             obj({"command": {"type": "string"}, "cwd": path, "timeout": {"type": "integer", "description": "seconds, max 300"}},
                 ["command"]), run_command, timeout=approval_timeout + 360),
        Tool("run_tests", "Run the project's tests (auto-detected: pytest, npm test, node --test, go test, cargo "
                          "test, make test) or a given test command.",
             obj({"command": {"type": "string"}, "cwd": path, "timeout": {"type": "integer"}}, []), run_tests,
             timeout=approval_timeout + 360),
        Tool("git_status", "Current branch and changed files.", obj({}, []), git_status, timeout=30),
        Tool("git_diff", "Unified diff of uncommitted changes (whole project or one path).",
             obj({"path": path}, []), git_diff, timeout=30),
        Tool("git_log", "Recent commits (optionally for one path).",
             obj({"limit": {"type": "integer"}, "path": path}, []), git_log, timeout=30),
        Tool("git_commit", "Commit changes (all, or the given paths) with a message. Asks the user for approval.",
             obj({"message": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}}, ["message"]),
             git_commit, timeout=approval_timeout, requires_approval=True),
    ]
