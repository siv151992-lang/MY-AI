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
    project_id: str | None = None   # start the chat inside a project
    agent_id: str | None = None     # chat with one of the user's custom agents


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageIn(BaseModel):
    content: str = Field(default="", max_length=100_000)
    file_ids: list[str] = Field(default_factory=list, max_length=10)
    model_id: str | None = None
    agent_mode: bool = False
    regenerate: bool = False
    skill_id: str | None = None     # apply a saved or built-in skill to this message


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
    from ..hub import custom_agents, projects
    if body.project_id and not await projects.get_project(user["id"], body.project_id):
        raise HTTPException(404, "Project not found")
    if body.agent_id and not await custom_agents.get_agent(user["id"], body.agent_id):
        raise HTTPException(404, "Agent not found")
    conv = await db.create_conversation(user["id"], body.model_id)
    if body.project_id or body.agent_id:
        await db.pool().execute("UPDATE conversations SET project_id=$3, agent_id=$4 WHERE id=$1 AND user_id=$2",
                                conv["id"], user["id"], body.project_id, body.agent_id)
    return {**conv, "project_id": body.project_id, "agent_id": body.agent_id}


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
    from ..documents import access, store as doc_store
    readable = await access.filter_readable(user["id"], file_ids)
    files = {f["id"]: {"id": f["id"], "filename": f["filename"], "kind": f["kind"], "mime_type": f["mime_type"]}
             for f in await doc_store.get_docs(user["id"], readable)}
    for m in msgs:
        m["files"] = [files[f] for f in m["file_ids"] if f in files]
    from ..hub import custom_agents, projects
    project = await projects.get_project(user["id"], str(conv["project_id"])) if conv.get("project_id") else None
    agent = await custom_agents.get_agent(user["id"], str(conv["agent_id"])) if conv.get("agent_id") else None
    return {"id": conv["id"], "title": conv["title"], "model_id": conv["model_id"], "messages": msgs,
            "project": {"id": project["id"], "name": project["name"]} if project else None,
            "agent": {"id": agent["id"], "name": agent["name"]} if agent else None}


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
    from ..documents import access
    body.file_ids = await access.filter_readable(user["id"], body.file_ids)  # never attach documents the user can't read

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
