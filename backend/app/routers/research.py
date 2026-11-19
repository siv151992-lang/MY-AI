"""Deep research API: start runs, stream progress, inspect sources and claims, export reports."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..research import report, store
from ..research.engine import DEPTHS
from ..research.manager import ResearchUnavailable, TooManyRuns, get_manager
from ..security import chat_rate_limit, current_user

router = APIRouter(prefix="/api/research", tags=["research"])


class ResearchIn(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    depth: Literal["quick", "standard", "deep"] = "standard"
    model_id: str | None = None


async def _owned(user: dict, run_id: str) -> dict:
    run = await store.get_run(user["id"], run_id)
    if not run:
        raise HTTPException(404, "Research run not found")
    return run


@router.get("/config")
async def config(user: dict = Depends(current_user)):
    ok, why = get_manager().available()
    return {"available": ok, "reason": why,
            "depths": {k: {"sources": d.max_sources, "subquestions": d.subquestions, "verification": d.verify_claims}
                       for k, d in DEPTHS.items()}}


@router.post("")
async def start(body: ResearchIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await get_manager().start(user["id"], body.question, body.depth, body.model_id)
    except ResearchUnavailable as e:
        raise HTTPException(503, str(e))
    except TooManyRuns as e:
        raise HTTPException(429, str(e))


@router.get("")
async def list_runs(user: dict = Depends(current_user)):
    return await store.list_runs(user["id"])


@router.get("/{run_id}")
async def get_run(run_id: str, user: dict = Depends(current_user)):
    run = await _owned(user, run_id)
    return {**run, "active": get_manager().is_active(run_id), "sources": await store.sources(run_id),
            "claims": await store.claims(run_id)}


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    if not get_manager().cancel(run_id):
        raise HTTPException(409, "This run is not in progress")
    return {"ok": True}


@router.delete("/{run_id}")
async def delete(run_id: str, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    get_manager().cancel(run_id)
    await store.delete_run(user["id"], run_id)
    return {"ok": True}


@router.get("/{run_id}/export")
async def export(run_id: str, format: Literal["md", "pdf"] = "md", user: dict = Depends(current_user)):
    run = await _owned(user, run_id)
    if run["status"] != "completed" or not run["report"]:
        raise HTTPException(409, "Only completed research runs have a report to export")
    name = (re.sub(r"[^A-Za-z0-9]+", "-", run["question"]).strip("-")[:60] or "research").lower()
    if format == "pdf":
        data = await asyncio.to_thread(report.to_pdf, run["report"], run["question"])
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return Response(run["report"], media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@router.get("/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0, user: dict = Depends(current_user)):
    """SSE: replays stored progress events after `after`, then streams live ones until the run ends."""
    m = get_manager()
    run = await _owned(user, run_id)
    es = store.EventStore()

    async def stream():
        q = m.bus.subscribe(run_id)
        last = after
        try:
            for ev in await es.list_events(run_id, after):
                last = ev["seq"]
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            if run["status"] in store.TERMINAL and not m.is_active(run_id):
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
