from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..llm import registry
from ..llm.formats import ProviderError
from ..security import chat_rate_limit, current_user
from ..services import chat

router = APIRouter(prefix="/api", tags=["conversations"])


class ConversationIn(BaseModel):
    model_id: str | None = None


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageIn(BaseModel):
    content: str = Field(default="", max_length=100_000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    model_id: str | None = None
    agent_mode: bool = False
    regenerate: bool = False


@router.get("/models")
async def models(user: dict = Depends(current_user)):
    try:
        default = registry.get_model(None).id
    except RuntimeError:
        default = None
    return {"default": default,
            "models": [{"id": m.id, "label": m.label, "provider": m.provider, "vision": m.vision, "tools": m.tools}
                       for m in registry.available_models()]}


@router.get("/conversations")
async def list_conversations(user: dict = Depends(current_user)):
    return await db.list_conversations(user["id"])


@router.post("/conversations")
async def create_conversation(body: ConversationIn, user: dict = Depends(current_user)):
    return await db.create_conversation(user["id"], body.model_id)


async def _owned(user: dict, conv_id: str) -> dict:
    try:
        conv = await db.get_conversation(user["id"], conv_id)
    except Exception:  # malformed UUID
        conv = None
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: dict = Depends(current_user)):
    conv = await _owned(user, conv_id)
    msgs = await db.list_messages(conv_id)
    file_ids = sorted({f for m in msgs for f in m["file_ids"]})
    files = {f["id"]: {"id": f["id"], "filename": f["filename"], "kind": f["kind"], "mime_type": f["mime_type"]}
             for f in await db.get_files(user["id"], file_ids)}
    for m in msgs:
        m["files"] = [files[f] for f in m["file_ids"] if f in files]
    return {"id": conv["id"], "title": conv["title"], "model_id": conv["model_id"], "messages": msgs}


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: ConversationPatch, user: dict = Depends(current_user)):
    await _owned(user, conv_id)
    await db.update_conversation(user["id"], conv_id, title=body.title.strip())
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: dict = Depends(current_user)):
    if not await db.delete_conversation(user["id"], conv_id):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: MessageIn, request: Request, user: dict = Depends(chat_rate_limit)):
    conv = await _owned(user, conv_id)
    if not body.regenerate and not body.content.strip() and not body.file_ids:
        raise HTTPException(422, "Message is empty")
    try:
        registry.get_model(body.model_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    async def stream():
        try:
            async for ev in chat.respond(user=user, conv=conv, text=body.content, file_ids=body.file_ids,
                                         model_id=body.model_id, agent_mode=body.agent_mode,
                                         regenerate=body.regenerate):
                if await request.is_disconnected():
                    break
                yield sse(ev)
        except ProviderError as e:
            yield sse({"type": "error", "message": str(e)})
        except Exception:
            import logging
            logging.getLogger("aria").exception("chat stream failed")
            yield sse({"type": "error", "message": "Something went wrong on the server. Please try again."})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
