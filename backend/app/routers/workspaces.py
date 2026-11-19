"""Coding workspace API: projects, files, search, terminal, git, checkpoints, approvals and the coding chat.

Destructive actions taken from the UI (delete, discard, restore a checkpoint, push) require `confirm: true`,
which the UI only sends after the user confirms in a dialog. Destructive actions requested by the AI go
through the approval gate instead (see workspace/approvals.py).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..llm import registry
from ..llm.formats import ProviderError
from ..security import chat_rate_limit, current_user
from ..workspace import git, index, runner, service, store
from ..workspace.approvals import gate
from ..workspace.fs import WorkspaceError, apply_edit, is_binary, normalize

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    clone_url: str | None = Field(default=None, max_length=500)


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=2_000_000)
    expected: str | None = None  # optional optimistic-concurrency check: content the editor loaded


class PathIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    confirm: bool = False


class MoveIn(BaseModel):
    from_: str = Field(alias="from", min_length=1, max_length=500)
    to: str = Field(min_length=1, max_length=500)


class ExecIn(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    cwd: str = Field(default="", max_length=500)
    timeout: int = Field(default=60, ge=1, le=300)


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    paths: list[str] = Field(default_factory=list, max_length=500)


class PathsConfirm(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    confirm: bool = False


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    create: bool = False


class RemoteIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)


class PushIn(BaseModel):
    token: str | None = Field(default=None, max_length=500)  # used for this push only, never stored
    username: str = Field(default="x-access-token", max_length=100)
    confirm: bool = False


class RestoreIn(BaseModel):
    sha: str = Field(min_length=40, max_length=40)
    confirm: bool = False


class DecisionIn(BaseModel):
    approved: bool


class ChatIn(BaseModel):
    content: str = Field(default="", max_length=100_000)
    conversation_id: str | None = None
    model_id: str | None = None
    regenerate: bool = False
    editor: dict | None = None  # {path, selection?, start_line?, end_line?}


def _bad(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


def _need_confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise HTTPException(428, f"{what} is destructive: resend with confirm=true after the user confirms.")


async def _ws(user: dict, ws_id: str) -> dict:
    ws = await store.get(user["id"], ws_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")
    return ws


async def _log(ws_id: str, user: dict, kind: str, summary: str, **detail) -> None:
    await store.log(ws_id, user["id"], "user", kind, summary, **detail)


# ---------------------------------------------------------------- workspaces
@router.get("")
async def list_workspaces(user: dict = Depends(current_user)):
    return await store.list_(user["id"])


@router.post("")
async def create_workspace(body: WorkspaceIn, user: dict = Depends(current_user)):
    try:
        return await service.create(user, body.name.strip(), body.description.strip(), clone_url=body.clone_url or None)
    except WorkspaceError as e:
        raise _bad(e)


@router.post("/import")
async def import_workspace(file: UploadFile, name: str = Query(min_length=1, max_length=100),
                           user: dict = Depends(current_user)):
    limit = get_settings().workspace_import_max_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "Archive too large")
    try:
        return await service.create(user, name.strip(), "", archive=data)
    except WorkspaceError as e:
        raise _bad(e)


@router.get("/{ws_id}")
async def get_workspace(ws_id: str, user: dict = Depends(current_user)):
    ws = await _ws(user, ws_id)
    return {**ws, "conversations": await store.conversations(user["id"], ws_id),
            "pending_approvals": gate().pending(ws_id, user["id"])}


@router.patch("/{ws_id}")
async def patch_workspace(ws_id: str, body: WorkspacePatch, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return await store.update(user["id"], ws_id, **body.model_dump(exclude_none=True))


@router.delete("/{ws_id}")
async def delete_workspace(ws_id: str, confirm: bool = False, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    _need_confirm(confirm, "Deleting a workspace")
    await service.delete(user, ws_id)
    return {"ok": True}


@router.get("/{ws_id}/export")
async def export_workspace(ws_id: str, user: dict = Depends(current_user)):
    ws = await _ws(user, ws_id)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in ws["name"])[:60] or "project"
    data = service.export_zip(service.fs(ws_id), safe)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


@router.get("/{ws_id}/activity")
async def activity(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return await store.activity(user["id"], ws_id)


# ---------------------------------------------------------------- files
@router.get("/{ws_id}/tree")
async def tree(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    w = service.fs(ws_id)
    st = await git.status(w.root)
    return {"entries": w.tree(), "changes": {f["path"]: f["status"] for f in st["files"]}, "branch": st["branch"]}


@router.get("/{ws_id}/file")
async def read_file(ws_id: str, path: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    try:
        data = service.fs(ws_id).read_bytes(path)
    except WorkspaceError as e:
        raise HTTPException(404 if "No such" in str(e) else 422, str(e))
    if is_binary(data):
        return {"path": normalize(path), "binary": True, "size": len(data), "content": None}
    return {"path": normalize(path), "binary": False, "size": len(data), "content": data.decode("utf-8", "replace")}


@router.get("/{ws_id}/raw")
async def raw_file(ws_id: str, path: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    try:
        data = service.fs(ws_id).read_bytes(path)
    except WorkspaceError as e:
        raise HTTPException(404, str(e))
    # Never render user content as HTML on our origin.
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"})


@router.put("/{ws_id}/file")
async def write_file(ws_id: str, body: FileWrite, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    w = service.fs(ws_id)
    async with service.lock(ws_id):
        try:
            if body.expected is not None and w.exists(body.path) and w.read_text(body.path) != body.expected:
                raise HTTPException(409, "The file changed since you opened it (probably edited by the AI). "
                                         "Reload it before saving.")
            r = w.write_text(body.path, body.content)
        except WorkspaceError as e:
            raise _bad(e)
    await _log(ws_id, user, "write", f"{'Created' if r['created'] else 'Saved'} {r['path']}", **r)
    return r


@router.post("/{ws_id}/mkdir")
async def mkdir(ws_id: str, body: PathIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    try:
        service.fs(ws_id).mkdir(body.path)
    except WorkspaceError as e:
        raise _bad(e)
    return {"ok": True}


@router.post("/{ws_id}/move")
async def move(ws_id: str, body: MoveIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    async with service.lock(ws_id):
        try:
            service.fs(ws_id).move(body.from_, body.to)
        except WorkspaceError as e:
            raise _bad(e)
    await _log(ws_id, user, "move", f"Moved {body.from_} → {body.to}")
    return {"ok": True}


@router.post("/{ws_id}/delete")
async def delete_path(ws_id: str, body: PathIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    _need_confirm(body.confirm, "Deleting files")
    async with service.lock(ws_id):
        try:
            removed = service.fs(ws_id).delete(body.path)
        except WorkspaceError as e:
            raise _bad(e)
    await _log(ws_id, user, "delete", f"Deleted {body.path}", paths=removed[:200])
    return {"deleted": removed}


@router.get("/{ws_id}/search")
async def search(ws_id: str, q: str = Query(min_length=1, max_length=500),
                 mode: str = Query(default="text", pattern="^(text|regex|relevant)$"),
                 glob: str | None = None, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    w = service.fs(ws_id)
    try:
        if mode == "relevant":
            return {"files": index.relevant_files(w, q, k=20)}
        return {"hits": index.grep(w, q, regex=mode == "regex", glob=glob, limit=300)}
    except WorkspaceError as e:
        raise _bad(e)


@router.post("/{ws_id}/edit-preview")
async def edit_preview(ws_id: str, body: dict, user: dict = Depends(current_user)):
    """Dry-run of an exact search/replace, used by the UI for find-and-replace."""
    await _ws(user, ws_id)
    try:
        before = service.fs(ws_id).read_text(str(body.get("path", "")))
        after, n = apply_edit(before, str(body.get("old_text", "")), str(body.get("new_text", "")),
                              bool(body.get("replace_all")))
    except WorkspaceError as e:
        raise _bad(e)
    return {"replacements": n, "content": after}


# ---------------------------------------------------------------- terminal
@router.post("/{ws_id}/exec")
async def exec_command(ws_id: str, body: ExecIn, user: dict = Depends(chat_rate_limit)):
    """Run a command typed by the user in the sandbox. The user ran it, so its writes and deletions apply."""
    await _ws(user, ws_id)
    s = get_settings()
    w = service.fs(ws_id)
    try:
        res = await runner.execute(w, body.command, cwd=body.cwd, timeout=body.timeout,
                                   sandbox_url=s.sandbox_url, token=s.sandbox_token)
    except WorkspaceError as e:
        raise _bad(e)
    except Exception as e:  # sandbox unreachable
        raise HTTPException(503, f"Sandbox unavailable: {type(e).__name__}")
    async with service.lock(ws_id):
        written = runner.apply_writes(w, res)
        deleted = runner.apply_deletes(w, res.deleted)
    await _log(ws_id, user, "command", f"$ {body.command[:200]} → exit {res.exit_code}", exit_code=res.exit_code)
    return {"exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr, "timed_out": res.timed_out,
            "written": written, "deleted": deleted, "skipped": res.skipped}


@router.get("/{ws_id}/test-command")
async def test_command(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return {"command": runner.detect_test_command(service.fs(ws_id))}


# ---------------------------------------------------------------- git
async def _git(fn, *args, **kw):
    try:
        return await fn(*args, **kw)
    except WorkspaceError as e:
        raise _bad(e)


@router.get("/{ws_id}/git/status")
async def git_status(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    root = service.root(ws_id)
    st = await _git(git.status, root)
    stats = await _git(git.numstat, root)
    for f in st["files"]:
        f["added"], f["removed"] = stats.get(f["path"], (0, 0))
    return st


@router.get("/{ws_id}/git/diff")
async def git_diff(ws_id: str, path: str | None = None, rev: str | None = Query(default=None, pattern="^[0-9a-f]{7,40}$"),
                   user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return {"diff": await _git(git.diff, service.root(ws_id), path, rev=rev)}


@router.get("/{ws_id}/git/log")
async def git_log(ws_id: str, limit: int = Query(default=50, ge=1, le=500), path: str | None = None,
                  user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return await _git(git.log, service.root(ws_id), limit, path)


@router.get("/{ws_id}/git/show/{sha}")
async def git_show(ws_id: str, sha: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return {"text": await _git(git.show, service.root(ws_id), sha)}


@router.post("/{ws_id}/git/commit")
async def git_commit(ws_id: str, body: CommitIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    async with service.lock(ws_id):
        c = await _git(git.commit_all, service.root(ws_id), body.message, paths=body.paths or None,
                       author=service.author(user))
    if not c:
        raise HTTPException(422, "Nothing to commit")
    await _log(ws_id, user, "commit", f"Committed {c['short']}: {c['message']}", sha=c["sha"])
    return c


@router.post("/{ws_id}/git/discard")
async def git_discard(ws_id: str, body: PathsConfirm, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    _need_confirm(body.confirm, "Discarding changes")
    async with service.lock(ws_id):
        cp = await _git(git.checkpoint, service.root(ws_id), "Before discarding changes")
        done = await _git(git.discard, service.root(ws_id), body.paths)
    await _log(ws_id, user, "discard", f"Discarded changes in {len(done)} file(s)", paths=done, checkpoint=cp)
    return {"discarded": done, "checkpoint": cp}


@router.get("/{ws_id}/git/branches")
async def git_branches(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return await _git(git.branches, service.root(ws_id))


@router.post("/{ws_id}/git/checkout")
async def git_checkout(ws_id: str, body: BranchIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    async with service.lock(ws_id):
        if body.create:
            await _git(git.create_branch, service.root(ws_id), body.name)
        else:
            await _git(git.checkout, service.root(ws_id), body.name)
    await _log(ws_id, user, "branch", f"{'Created' if body.create else 'Switched to'} branch {body.name}")
    return await _git(git.branches, service.root(ws_id))


@router.put("/{ws_id}/git/remote")
async def git_remote(ws_id: str, body: RemoteIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    await _git(git.set_remote, service.root(ws_id), body.url)
    await _log(ws_id, user, "remote", f"Set remote to {body.url}")
    return {"ok": True}


@router.post("/{ws_id}/git/push")
async def git_push(ws_id: str, body: PushIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    _need_confirm(body.confirm, "Publishing (push)")
    async with service.lock(ws_id):
        out = await _git(git.push, service.root(ws_id), token=body.token, username=body.username)
    await _log(ws_id, user, "push", "Pushed to origin")
    return {"output": out}


# ---------------------------------------------------------------- checkpoints & approvals
@router.get("/{ws_id}/checkpoints")
async def checkpoints(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return (await _git(git.list_checkpoints, service.root(ws_id)))[:100]


@router.get("/{ws_id}/checkpoints/{sha}/changes")
async def checkpoint_changes(ws_id: str, sha: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    if len(sha) != 40:
        raise HTTPException(422, "Invalid checkpoint")
    root = service.root(ws_id)
    files = await _git(git.changed_since, root, sha)
    now = await _git(git.checkpoint_tree, root)
    d = (await _git(git.git, root, "diff", "--no-color", "--no-ext-diff", sha, now)).out
    return {"files": files, "diff": d[:500_000]}


@router.post("/{ws_id}/checkpoints/restore")
async def restore(ws_id: str, body: RestoreIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    _need_confirm(body.confirm, "Restoring a checkpoint")
    async with service.lock(ws_id):
        changed = await _git(git.restore_checkpoint, service.root(ws_id), body.sha)
    await _log(ws_id, user, "restore", f"Restored checkpoint {body.sha[:8]} ({len(changed)} files)", sha=body.sha)
    return {"changed": changed}


@router.get("/{ws_id}/approvals")
async def approvals(ws_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    return gate().pending(ws_id, user["id"])


@router.post("/{ws_id}/approvals/{approval_id}")
async def decide(ws_id: str, approval_id: str, body: DecisionIn, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    a = gate().decide(user["id"], approval_id, body.approved)
    if not a or a.workspace_id != ws_id:
        raise HTTPException(404, "No pending approval with that id (it may have expired)")
    return {"id": a.id, "status": a.status}


# ---------------------------------------------------------------- coding chat
def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@router.get("/{ws_id}/conversations/{conv_id}")
async def get_conversation(ws_id: str, conv_id: str, user: dict = Depends(current_user)):
    await _ws(user, ws_id)
    conv = await db.get_conversation(user["id"], conv_id)
    if not conv or conv.get("workspace_id") != ws_id:
        raise HTTPException(404, "Conversation not found")
    return {"id": conv["id"], "title": conv["title"], "messages": await db.list_messages(conv_id)}


@router.post("/{ws_id}/chat")
async def chat(ws_id: str, body: ChatIn, request: Request, user: dict = Depends(chat_rate_limit)):
    ws = await _ws(user, ws_id)
    if not body.regenerate and not body.content.strip():
        raise HTTPException(422, "Message is empty")
    try:
        registry.get_model(body.model_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if body.conversation_id:
        conv = await db.get_conversation(user["id"], body.conversation_id)
        if not conv or conv.get("workspace_id") != ws_id:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = await db.get_conversation(user["id"], (await store.create_conversation(user["id"], ws_id, body.model_id))["id"])

    async def stream():
        yield _sse({"type": "conversation", "id": conv["id"]})
        try:
            async for ev in service.respond(user=user, ws_row=ws, conv=conv, text=body.content, model_id=body.model_id,
                                            editor=body.editor, regenerate=body.regenerate):
                if await request.is_disconnected():
                    break
                yield _sse(ev)
        except ProviderError as e:
            yield _sse({"type": "error", "message": str(e)})
        except WorkspaceError as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception:
            import logging
            logging.getLogger("aria").exception("workspace chat failed")
            yield _sse({"type": "error", "message": "Something went wrong on the server. Please try again."})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
