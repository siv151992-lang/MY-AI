from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..autonomy.manager import TooManyRuns, build_agent_tools, get_manager
from ..autonomy.models import TERMINAL
from ..llm import registry
from ..security import current_user, limiter

router = APIRouter(prefix="/api/agent", tags=["agent"])


class RunIn(BaseModel):
    goal: str = Field(min_length=5, max_length=8000)
    model_id: str | None = None


class DecisionIn(BaseModel):
    approved: bool


@router.get("/tools")
async def tools(user: dict = Depends(current_user)):
    """What the agent can really do on this server (and which actions need approval)."""
    return build_agent_tools().catalog()


@router.post("/runs")
async def start_run(body: RunIn, user: dict = Depends(current_user)):
    limiter.check(f"agent:{user['id']}", 10)
    try:
        registry.get_model(body.model_id)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    try:
        run = await get_manager().start(user["id"], body.goal, body.model_id)
    except TooManyRuns as e:
        raise HTTPException(429, str(e))
    return run.to_dict()


@router.get("/runs")
async def list_runs(user: dict = Depends(current_user)):
    return [{k: v for k, v in r.to_dict().items() if k != "report"} for r in await get_manager().store.list_runs(user["id"])]


async def _owned(user: dict, run_id: str):
    run = await get_manager().store.get_run(user["id"], run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: dict = Depends(current_user)):
    m = get_manager()
    run = await _owned(user, run_id)
    return {**run.to_dict(), "active": m.is_active(run_id),
            "steps": [s.to_dict() for s in await m.store.list_steps(run_id)],
            "approvals": [a.to_dict() for a in await m.store.list_approvals(run_id)],
            "actions": [a.to_dict() for a in await m.store.list_actions(run_id)]}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    if not await get_manager().cancel(user["id"], run_id):
        raise HTTPException(409, "Run is not active")
    return {"ok": True}


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def decide(run_id: str, approval_id: str, body: DecisionIn, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    if not await get_manager().decide(user["id"], run_id, approval_id, body.approved):
        raise HTTPException(409, "This approval is no longer pending")
    return {"ok": True}


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0, user: dict = Depends(current_user)):
    """SSE: replays stored events after `after`, then streams live ones until the run ends."""
    m = get_manager()
    run = await _owned(user, run_id)

    async def stream():
        q = m.bus.subscribe(run_id)  # subscribe before replay so nothing is missed
        last = after
        try:
            for ev in await m.store.list_events(run_id, after):
                last = ev["seq"]
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            if run.status in TERMINAL and not m.is_active(run_id):
                yield "event: end\ndata: {}\n\n"
                return
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    if not m.is_active(run_id):
                        yield "event: end\ndata: {}\n\n"
                        return
                    continue
                if ev["seq"] <= last:
                    continue
                last = ev["seq"]
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                if ev["type"] == "status" and ev["data"].get("final"):
                    yield "event: end\ndata: {}\n\n"
                    return
        finally:
            m.bus.unsubscribe(run_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
