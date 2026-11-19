from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..llm import registry
from ..security import current_user

router = APIRouter(prefix="/api/memories", tags=["memory"])


class MemoryIn(BaseModel):
    content: str = Field(min_length=2, max_length=500)


@router.get("")
async def list_memories(user: dict = Depends(current_user)):
    return await db.list_memories(user["id"])


@router.post("")
async def add_memory(body: MemoryIn, user: dict = Depends(current_user)):
    emb = await registry.embed([body.content])
    return await db.add_memory(user["id"], body.content.strip(), emb[0] if emb else None, None)


@router.delete("/{mem_id}")
async def delete_memory(mem_id: str, user: dict = Depends(current_user)):
    if not await db.delete_memory(user["id"], mem_id):
        raise HTTPException(404, "Memory not found")
    return {"ok": True}
