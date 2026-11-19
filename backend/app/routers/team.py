"""Multi-agent orchestration API: start a team run, stream which agent is doing what, inspect and export results."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..security import chat_rate_limit, current_user
from ..team import store
from ..team.manager import TooManyRuns, get_manager
from ..team.specialists import DESCRIPTION, LABEL, SPECS

router = APIRouter(prefix="/api/team", tags=["team"])


class RunIn(BaseModel):
    goal: str = Field(min_length=5, max_length=8000)
    model_id: str | None = None


async def _owned(user: dict, run_id: str) -> dict:
    run = await store.get_run(user["id"], run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/agents")
async def agents(user: dict = Depends(current_user)):
    pool = get_manager().pool()
    out = [{"name": "planning", "label": LABEL["planning"], "description": "breaks the goal into tasks and assigns agents",
            "tools": [], "available": True, "side_effects": False}]
    for name, spec in SPECS.items():
        have = [t for t in spec.tools if t in pool] + (spec.tools if name == "browser" else [])
        out.append({"name": name, "label": LABEL[name], "description": DESCRIPTION[name], "tools": sorted(set(have)),
                    "missing": [t for t in spec.tools if t not in pool and name != "browser"],
                    "available": bool(have) or not spec.requires_tools, "side_effects": spec.side_effects})
    from ..hub import custom_agents
    for key, spec in (await custom_agents.team_specs(user["id"])).items():
        out.append({"name": key, "label": spec.label, "description": spec.description, "tools": spec.tools,
                    "missing": [t for t in spec.tools if t not in pool and not t.startswith("api_")],
                    "available": True, "side_effects": spec.side_effects, "custom": True})
    return out


@router.post("/runs")
async def start(body: RunIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await get_manager().start(user["id"], body.goal, body.model_id)
    except TooManyRuns as e:
        raise HTTPException(429, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@router.get("/runs")
async def list_runs(user: dict = Depends(current_user)):
    return await store.list_runs(user["id"])


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: dict = Depends(current_user)):
    run = await _owned(user, run_id)
    return {**run, "active": get_manager().is_active(run_id), "tasks": await store.tasks(run_id)}


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    if not get_manager().cancel(run_id):
        raise HTTPException(409, "This run is not in progress")
    return {"ok": True}


@router.delete("/runs/{run_id}")
async def delete(run_id: str, user: dict = Depends(current_user)):
    await _owned(user, run_id)
    get_manager().cancel(run_id)
    await store.delete_run(user["id"], run_id)
    return {"ok": True}


@router.get("/runs/{run_id}/export")
async def export(run_id: str, user: dict = Depends(current_user)):
    run = await _owned(user, run_id)
    if not run["final"]:
        raise HTTPException(409, "This run has no final answer")
    tasks = await store.tasks(run_id)
    v = run["verification"] or {}
    md = [run["final"], "\n---\n", "## How this was produced",
          *[f"- **{t['id']} · {LABEL.get(t['agent'], t['agent'].replace('_', ' '))}** — {t['title']} ({t['status']}"
            + (f", {t['confidence']} confidence" if t["confidence"] else "") + ")"
            + (f"; sources: {', '.join(t['sources'])}" if t["sources"] else "") for t in tasks]]
    if run["conflicts"]:
        md += ["", "### Conflicts between agents", *[f"- {c['topic']} — {c['status']}" for c in run["conflicts"]]]
    md += ["", f"### Verification: {'passed' if v.get('ok') else 'issues found'}", *[f"- {i}" for i in v.get("issues", [])]]
    name = (re.sub(r"[^A-Za-z0-9]+", "-", run["goal"]).strip("-")[:60] or "team-run").lower()
    return Response("\n".join(md) + "\n", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0, user: dict = Depends(current_user)):
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

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
