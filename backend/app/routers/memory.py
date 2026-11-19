"""Memory API: inspect, search, correct, approve, export, import and delete everything the assistant remembers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import db
from ..memory import service, store
from ..memory.scoring import CATEGORIES
from ..security import current_user

router = APIRouter(prefix="/api/memories", tags=["memory"])
conversation_router = APIRouter(prefix="/api/conversations", tags=["memory"])

Category = Literal["instruction", "profile", "preference", "project", "relationship", "fact", "other"]


class MemoryIn(BaseModel):
    content: str = Field(min_length=2, max_length=500)
    category: Category | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    pinned: bool = False


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=2, max_length=500)
    category: Category | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    pinned: bool | None = None
    status: Literal["active", "archived"] | None = None


class ApproveIn(BaseModel):
    content: str | None = Field(default=None, min_length=2, max_length=500)


class ReviewIn(BaseModel):
    approve: list[str] = Field(default_factory=list, max_length=200)
    reject: list[str] = Field(default_factory=list, max_length=200)


class BulkDeleteIn(BaseModel):
    scope: Literal["all", "active", "pending", "archived"] | None = None
    ids: list[str] = Field(default_factory=list, max_length=1000)
    include_conversation_memory: bool = False
    confirm: bool = False


class SettingsIn(BaseModel):
    memory_enabled: bool | None = None
    auto_extract: bool | None = None
    require_approval: bool | None = None
    use_in_chat: bool | None = None
    allow_sensitive: bool | None = None
    store_source_excerpts: bool | None = None
    summarize_conversations: bool | None = None
    cross_conversation_recall: bool | None = None
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    pending_expiry_days: int | None = Field(default=None, ge=1, le=365)


def _err(e: Exception) -> HTTPException:
    if isinstance(e, service.MemoryNotFound):
        return HTTPException(404, "Memory not found")
    return HTTPException(422, str(e))


# ----- collection -----
@router.get("")
async def list_memories(user: dict = Depends(current_user),
                        status: Literal["active", "pending", "archived", "all"] = "active",
                        category: Category | None = None,
                        q: str | None = Query(default=None, max_length=500),
                        sort: Literal["recent", "importance", "used"] = "recent"):
    statuses = store.STATUSES if status == "all" else (status,)
    if q and q.strip():
        items = await service.search(user["id"], q, k=50, statuses=statuses)
        if category:
            items = [m for m in items if m["category"] == category]
        for m in items:
            m.pop("keyword", None)
    else:
        items = await store.list_(user["id"], statuses=statuses, category=category, sort=sort)
    return {"items": items, "counts": await store.counts(user["id"]), "categories": list(CATEGORIES)}


@router.post("")
async def add_memory(body: MemoryIn, user: dict = Depends(current_user)):
    try:
        return await service.create(user["id"], body.content, source="manual", category=body.category,
                                    importance=body.importance, pinned=body.pinned)
    except service.MemoryRejected as e:
        raise _err(e)


@router.post("/review")
async def review(body: ReviewIn, user: dict = Depends(current_user)):
    return await service.review(user["id"], body.approve, body.reject)


@router.post("/delete")
async def bulk_delete(body: BulkDeleteIn, user: dict = Depends(current_user)):
    if not body.confirm:
        raise HTTPException(422, "Set confirm=true to delete memories in bulk.")
    if not body.ids and not body.scope:
        raise HTTPException(422, "Give either ids or a scope.")
    try:
        return await service.delete_bulk(user["id"], body.scope or "all", ids=body.ids or None,
                                         include_conversation_memory=body.include_conversation_memory)
    except service.MemoryRejected as e:
        raise _err(e)


# ----- privacy settings, audit, export/import -----
@router.get("/settings")
async def get_settings(user: dict = Depends(current_user)):
    return (await service.get_settings(user["id"])).to_dict()


@router.put("/settings")
async def put_settings(body: SettingsIn, user: dict = Depends(current_user)):
    patch = body.model_dump(exclude_unset=True)
    if "retention_days" in patch and not patch["retention_days"]:
        patch["retention_days"] = None
    return (await service.update_settings(user["id"], patch)).to_dict()


@router.get("/audit")
async def audit(user: dict = Depends(current_user), limit: int = Query(default=100, ge=1, le=1000)):
    return await store.audit_log(user["id"], limit)


@router.get("/export")
async def export(user: dict = Depends(current_user), format: Literal["json", "markdown"] = "json"):
    data = await service.export(user["id"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    if format == "markdown":
        return Response(service.export_markdown(data), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="aria-memory-{stamp}.md"'})
    return Response(json.dumps(data, default=str, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="aria-memory-{stamp}.json"'})


@router.post("/import")
async def import_memories(file: UploadFile, user: dict = Depends(current_user)):
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Import file is larger than 5 MB.")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Import file is not valid JSON.")
    try:
        return await service.import_memories(user["id"], data)
    except service.MemoryRejected as e:
        raise _err(e)


# ----- single memory -----
@router.get("/{mem_id}")
async def get_memory(mem_id: str, user: dict = Depends(current_user)):
    try:
        return await service.detail(user["id"], mem_id)
    except service.MemoryNotFound as e:
        raise _err(e)


@router.patch("/{mem_id}")
async def edit_memory(mem_id: str, body: MemoryPatch, user: dict = Depends(current_user)):
    try:
        return await service.update(user["id"], mem_id, body.model_dump(exclude_unset=True))
    except (service.MemoryRejected, service.MemoryNotFound) as e:
        raise _err(e)


@router.post("/{mem_id}/approve")
async def approve(mem_id: str, body: ApproveIn | None = None, user: dict = Depends(current_user)):
    try:
        return await service.approve(user["id"], mem_id, body.content if body else None)
    except (service.MemoryRejected, service.MemoryNotFound) as e:
        raise _err(e)


@router.post("/{mem_id}/reject")
async def reject(mem_id: str, user: dict = Depends(current_user)):
    try:
        await service.reject(user["id"], mem_id)
    except service.MemoryNotFound as e:
        raise _err(e)
    return {"ok": True}


@router.delete("/{mem_id}")
async def delete_memory(mem_id: str, user: dict = Depends(current_user)):
    try:
        await service.delete(user["id"], mem_id)
    except service.MemoryNotFound as e:
        raise _err(e)
    return {"ok": True}


# ----- per-conversation memory (short-term + summaries) -----
class ConversationMemoryPatch(BaseModel):
    memory_enabled: bool | None = None
    summary: str | None = Field(default=None, max_length=8000)


async def _conv(user: dict, conv_id: str) -> dict:
    try:
        conv = await db.get_conversation(user["id"], conv_id)
    except Exception:
        conv = None
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@conversation_router.get("/{conv_id}/memory")
async def conversation_memory(conv_id: str, user: dict = Depends(current_user)):
    return await service.conversation_memory(user["id"], await _conv(user, conv_id))


@conversation_router.patch("/{conv_id}/memory")
async def patch_conversation_memory(conv_id: str, body: ConversationMemoryPatch, user: dict = Depends(current_user)):
    conv = await _conv(user, conv_id)
    if body.memory_enabled is not None:
        await store.set_conversation_memory_enabled(user["id"], conv_id, body.memory_enabled)
    if body.summary is not None:  # the user corrects the rolling summary
        await db.update_conversation(user["id"], conv_id, summary=body.summary.strip() or None)
        await store.audit(user["id"], "summary_edited", None, conversation_id=conv_id)
    return await service.conversation_memory(user["id"], await _conv(user, conv["id"]))


@conversation_router.delete("/{conv_id}/memory")
async def reset_conversation_memory(conv_id: str, user: dict = Depends(current_user)):
    await _conv(user, conv_id)
    await store.clear_conversation_memory(user["id"], conv_id)
    await store.audit(user["id"], "conversation_memory_cleared", None, conversation_id=conv_id)
    return {"ok": True}
