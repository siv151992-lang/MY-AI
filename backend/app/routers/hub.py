"""Platform hub API: routing, analytics, projects, skills, custom agents, integrations, automations, tasks, voice."""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..hub import (automations, custom_agents, executor, integrations, projects, routing, skills, tasks, usage,
                   voice)
from ..llm import registry
from ..security import chat_rate_limit, current_user, limiter

router = APIRouter(prefix="/api", tags=["hub"])


def _bad(e: Exception, code: int = 422):
    return HTTPException(code, str(e))


# ------------------------------------------------------------------ routing
class RouteIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    has_images: bool = False
    has_documents: bool = False


@router.post("/router/route")
async def route(body: RouteIn, user: dict = Depends(current_user)):
    """Explain how a request would be routed (model, tools, workspace) without running it."""
    r = await routing.route_for_user(user["id"], body.text, has_images=body.has_images, has_documents=body.has_documents)
    labels = {m.id: m.label for m in registry.available_models()}
    return {**r.public(), "model_label": labels.get(r.model_id or "", None),
            "fallback_labels": [labels.get(f, f) for f in r.fallbacks]}


class RoutingPrefs(BaseModel):
    priority: str = "balanced"
    local_only: bool = False
    pinned: dict[str, str] = Field(default_factory=dict)


@router.get("/router/prefs")
async def get_prefs(user: dict = Depends(current_user)):
    return {"routing": (await routing.get_prefs(user["id"]))["routing"], "categories": list(routing.CATEGORIES),
            "models": [{"id": m.id, "label": m.label, "tier": m.tier, "provider": m.provider}
                       for m in registry.available_models()],
            "health": await usage.model_health()}


@router.put("/router/prefs")
async def put_prefs(body: RoutingPrefs, user: dict = Depends(current_user)):
    return (await routing.set_routing_prefs(user["id"], body.model_dump()))["routing"]


# ------------------------------------------------------------------ analytics
@router.get("/analytics")
async def analytics(days: int = Query(30, ge=1, le=365), scope: str = "me", user: dict = Depends(current_user)):
    from ..setup import is_owner
    if scope == "all" and not await is_owner(user["id"]):
        raise HTTPException(403, "Only the owner (first account) can see everyone's usage.")
    data = await usage.summary(None if scope == "all" else user["id"], days)
    who = None if scope == "all" else user["id"]
    data["tasks"] = await _task_outcomes(who, days)
    data["routing"] = await _routing_stats(who, days)
    data["can_view_all"] = await is_owner(user["id"])
    return data


async def _task_outcomes(user_id: str | None, days: int) -> list[dict]:
    out = []
    for kind, table in (("agent", "agent_runs"), ("research", "research_runs"), ("team", "team_runs"),
                        ("automation", "automation_runs")):
        rows = await db.pool().fetch(
            f"SELECT status, count(*) n, avg(extract(epoch FROM (coalesce(finished_at, updated_at) - created_at))) secs "
            f"FROM {table} WHERE created_at > now() - make_interval(days => $1) AND ($2::uuid IS NULL OR user_id=$2) "
            f"GROUP BY status", days, user_id)
        out.append({"kind": kind, "by_status": {r["status"]: r["n"] for r in rows},
                    "total": sum(r["n"] for r in rows),
                    "avg_seconds": int(sum((r["secs"] or 0) * r["n"] for r in rows) / max(1, sum(r["n"] for r in rows)))})
    return out


async def _routing_stats(user_id: str | None, days: int) -> dict:
    rows = await db.pool().fetch(
        "SELECT m.meta->'route'->>'category' AS category, m.meta->'route'->>'model_id' AS model, "
        "jsonb_array_length(coalesce(m.meta->'route'->'fallbacks_used', '[]'::jsonb)) AS fb, count(*) n "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE m.role='assistant' "
        "AND m.meta ? 'route' AND m.created_at > now() - make_interval(days => $1) AND ($2::uuid IS NULL OR c.user_id=$2) "
        "GROUP BY 1,2,3", days, user_id)
    by_cat: dict[str, dict[str, int]] = {}
    fallbacks = 0
    for r in rows:
        by_cat.setdefault(r["category"] or "chat", {})
        by_cat[r["category"] or "chat"][r["model"]] = by_cat[r["category"] or "chat"].get(r["model"], 0) + r["n"]
        fallbacks += r["n"] if r["fb"] else 0
    return {"decisions": sum(r["n"] for r in rows), "by_category": by_cat, "with_fallback": fallbacks}


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=20_000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=20_000)
    archived: bool | None = None


class AssignIn(BaseModel):
    kind: str
    id: str
    project_id: str | None = None


@router.get("/projects")
async def list_projects(archived: bool = False, user: dict = Depends(current_user)):
    return await projects.list_projects(user["id"], archived)


@router.post("/projects")
async def create_project(body: ProjectIn, user: dict = Depends(current_user)):
    return await projects.create_project(user["id"], body.name, body.description, body.instructions)


@router.get("/projects/{pid}")
async def get_project(pid: str, user: dict = Depends(current_user)):
    p = await projects.get_project(user["id"], pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return {**p, "items": await projects.items(user["id"], pid)}


@router.patch("/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch, user: dict = Depends(current_user)):
    p = await projects.update_project(user["id"], pid, **body.model_dump())
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@router.delete("/projects/{pid}")
async def delete_project(pid: str, user: dict = Depends(current_user)):
    if not await projects.delete_project(user["id"], pid):
        raise HTTPException(404, "Project not found")
    return {"ok": True}


@router.post("/projects/assign")
async def assign(body: AssignIn, user: dict = Depends(current_user)):
    try:
        ok = await projects.assign(user["id"], body.kind, body.id, body.project_id)
    except ValueError as e:
        raise _bad(e)
    if not ok:
        raise HTTPException(404, "Item not found")
    return {"ok": True}


# ------------------------------------------------------------------ tools catalog
@router.get("/tools")
async def tool_catalog(user: dict = Depends(current_user)):
    """Tools that actually work on this server for this user (for skill/agent/automation builders)."""
    return await executor.tool_catalog(user["id"])


# ------------------------------------------------------------------ skills
class SkillIn(BaseModel):
    name: str
    description: str = ""
    instructions: str
    inputs: list[dict] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    model_id: str | None = None
    project_id: str | None = None


class SkillRun(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    model_id: str | None = None


@router.get("/skills")
async def list_skills(user: dict = Depends(current_user)):
    return await skills.list_skills(user["id"])


@router.post("/skills")
async def create_skill(body: SkillIn, user: dict = Depends(current_user)):
    try:
        return await skills.create_skill(user["id"], body.model_dump())
    except skills.SkillError as e:
        raise _bad(e)


@router.put("/skills/{sid}")
async def update_skill(sid: str, body: SkillIn, user: dict = Depends(current_user)):
    if sid.startswith("builtin:"):
        raise HTTPException(400, "Built-in skills can't be edited; duplicate it instead.")
    try:
        s = await skills.update_skill(user["id"], sid, body.model_dump())
    except skills.SkillError as e:
        raise _bad(e)
    if not s:
        raise HTTPException(404, "Skill not found")
    return s


@router.delete("/skills/{sid}")
async def delete_skill(sid: str, user: dict = Depends(current_user)):
    if not await skills.delete_skill(user["id"], sid):
        raise HTTPException(404, "Skill not found")
    return {"ok": True}


@router.post("/skills/{sid}/run")
async def run_skill(sid: str, body: SkillRun, user: dict = Depends(chat_rate_limit)):
    sk = await skills.get_skill(user["id"], sid)
    if not sk:
        raise HTTPException(404, "Skill not found")
    try:
        return await skills.run(user["id"], sk, body.values, model_id=body.model_id)
    except skills.SkillError as e:
        raise _bad(e)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ------------------------------------------------------------------ custom agents
class AgentIn(BaseModel):
    name: str
    description: str = ""
    instructions: str
    tools: list[str] = Field(default_factory=list)
    model_id: str | None = None
    use_memory: bool = True
    in_team: bool = True


class AgentTask(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)


@router.get("/agents")
async def list_agents(user: dict = Depends(current_user)):
    return await custom_agents.list_agents(user["id"])


@router.post("/agents")
async def create_agent(body: AgentIn, user: dict = Depends(current_user)):
    try:
        return await custom_agents.create_agent(user["id"], body.model_dump())
    except custom_agents.AgentError as e:
        raise _bad(e)


@router.put("/agents/{aid}")
async def update_agent(aid: str, body: AgentIn, user: dict = Depends(current_user)):
    try:
        a = await custom_agents.update_agent(user["id"], aid, body.model_dump())
    except custom_agents.AgentError as e:
        raise _bad(e)
    if not a:
        raise HTTPException(404, "Agent not found")
    return a


@router.delete("/agents/{aid}")
async def delete_agent(aid: str, user: dict = Depends(current_user)):
    if not await custom_agents.delete_agent(user["id"], aid):
        raise HTTPException(404, "Agent not found")
    return {"ok": True}


@router.post("/agents/{aid}/run")
async def run_agent_task(aid: str, body: AgentTask, user: dict = Depends(chat_rate_limit)):
    a = await custom_agents.get_agent(user["id"], aid)
    if not a:
        raise HTTPException(404, "Agent not found")
    try:
        return await custom_agents.run_task(user["id"], a, body.task)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ------------------------------------------------------------------ integrations
class IntegrationIn(BaseModel):
    name: str
    description: str = ""
    base_url: str
    auth_type: str = "none"
    auth_name: str = ""
    secret: str | None = Field(default=None, max_length=4000)
    allow_writes: bool = False
    test_path: str = ""


@router.get("/integrations")
async def list_integrations(user: dict = Depends(current_user)):
    return {"custom": await integrations.list_integrations(user["id"]), "builtin": integrations.builtin_status()}


@router.post("/integrations")
async def create_integration(body: IntegrationIn, user: dict = Depends(current_user)):
    try:
        return await integrations.create_integration(user["id"], body.model_dump())
    except integrations.IntegrationError as e:
        raise _bad(e)


@router.put("/integrations/{iid}")
async def update_integration(iid: str, body: IntegrationIn, user: dict = Depends(current_user)):
    try:
        r = await integrations.update_integration(user["id"], iid, body.model_dump())
    except integrations.IntegrationError as e:
        raise _bad(e)
    if not r:
        raise HTTPException(404, "Integration not found")
    return r


@router.delete("/integrations/{iid}")
async def delete_integration(iid: str, user: dict = Depends(current_user)):
    if not await integrations.delete_integration(user["id"], iid):
        raise HTTPException(404, "Integration not found")
    return {"ok": True}


@router.post("/integrations/{iid}/test")
async def test_integration(iid: str, user: dict = Depends(current_user)):
    limiter.check(f"itest:{user['id']}", 20)
    try:
        return await integrations.test(user["id"], iid)
    except integrations.IntegrationError as e:
        raise HTTPException(404, str(e))


# ------------------------------------------------------------------ automations
class AutomationIn(BaseModel):
    name: str
    enabled: bool = True
    trigger_type: str = "manual"
    schedule: str = ""
    timezone: str = "UTC"
    steps: list[dict]
    project_id: str | None = None


def _hook_url(request: Request, aid, token: str) -> str:
    """Path only: the backend sits behind the web app's proxy, so the browser adds the public origin it uses."""
    return f"/api/hooks/{aid}/{token}"


@router.get("/automations")
async def list_automations(user: dict = Depends(current_user)):
    return await automations.list_automations(user["id"])


@router.post("/automations")
async def create_automation(body: AutomationIn, request: Request, user: dict = Depends(current_user)):
    try:
        a, token = await automations.create_automation(user["id"], body.model_dump())
    except automations.AutomationError as e:
        raise _bad(e)
    return {**a, "webhook_url": _hook_url(request, a["id"], token) if token else None}


@router.get("/automations/{aid}")
async def get_automation(aid: str, user: dict = Depends(current_user)):
    a = await automations.get_automation(user["id"], aid)
    if not a:
        raise HTTPException(404, "Automation not found")
    return {**a, "runs": await automations.list_runs(user["id"], aid, 20)}


@router.put("/automations/{aid}")
async def update_automation(aid: str, body: AutomationIn, request: Request, user: dict = Depends(current_user)):
    try:
        a, token = await automations.update_automation(user["id"], aid, body.model_dump())
    except automations.AutomationError as e:
        raise _bad(e)
    if not a:
        raise HTTPException(404, "Automation not found")
    return {**a, "webhook_url": _hook_url(request, a["id"], token) if token else None}


@router.delete("/automations/{aid}")
async def delete_automation(aid: str, user: dict = Depends(current_user)):
    if not await automations.delete_automation(user["id"], aid):
        raise HTTPException(404, "Automation not found")
    return {"ok": True}


@router.post("/automations/{aid}/webhook")
async def rotate_webhook(aid: str, request: Request, user: dict = Depends(current_user)):
    token = await automations.rotate_webhook(user["id"], aid)
    if not token:
        raise HTTPException(404, "Webhook automation not found")
    return {"webhook_url": _hook_url(request, aid, token)}


@router.post("/automations/{aid}/run")
async def run_automation(aid: str, user: dict = Depends(chat_rate_limit)):
    raw = await db.pool().fetchrow("SELECT * FROM automations WHERE id=$1 AND user_id=$2", aid, user["id"]) \
        if _uuid(aid) else None
    if not raw:
        raise HTTPException(404, "Automation not found")
    if any(s.get("type") in ("prompt", "skill", "agent", "research", "team") for s in automations._row(raw)["steps"]):
        try:
            registry.get_model(None)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
    try:
        return await automations.get_runner().start(dict(raw), "manual")
    except automations.AutomationError as e:
        raise HTTPException(429, str(e))


@router.get("/automation-runs")
async def automation_runs(user: dict = Depends(current_user)):
    return await automations.list_runs(user["id"])


@router.get("/automation-runs/{rid}")
async def automation_run(rid: str, user: dict = Depends(current_user)):
    r = await automations.get_run(user["id"], rid)
    if not r:
        raise HTTPException(404, "Run not found")
    return r


@router.post("/hooks/{aid}/{token}")
async def webhook(aid: str, token: str, request: Request):
    """Public trigger for webhook automations. The secret token in the URL authenticates the caller."""
    limiter.check(f"hook:{hashlib.sha256(aid.encode()).hexdigest()[:16]}", 30)
    if not _uuid(aid):
        raise HTTPException(404, "Not found")
    a = await automations.check_webhook(aid, token)
    if not a:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if len(raw) > 256_000:
        raise HTTPException(413, "Payload too large")
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw.decode(errors="replace")
    try:
        run = await automations.get_runner().start(a, "webhook", payload)
    except automations.AutomationError as e:
        raise HTTPException(429, str(e))
    return {"ok": True, "run_id": str(run["id"])}


def _uuid(v: str) -> bool:
    import uuid
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ tasks
@router.get("/tasks")
async def list_tasks(user: dict = Depends(current_user)):
    return await tasks.snapshot(user["id"])


@router.get("/tasks/stream")
async def stream_tasks(request: Request, user: dict = Depends(current_user)):
    """Server-sent events: a fresh snapshot whenever anything changes (checked every 1.5 s)."""
    async def gen():
        last, idle = None, 0
        while not await request.is_disconnected():
            snap = await tasks.snapshot(user["id"])
            data = json.dumps(snap, default=str)
            if data != last:
                last, idle = data, 0
                yield f"event: snapshot\ndata: {data}\n\n"
            else:
                idle += 1
                if idle % 10 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{kind}/{rid}/cancel")
async def cancel_task(kind: str, rid: str, user: dict = Depends(current_user)):
    if kind not in tasks.SOURCES or not _uuid(rid):
        raise HTTPException(404, "Task not found")
    if not await tasks.cancel(user["id"], kind, rid):
        raise HTTPException(409, "That task isn't running (or isn't yours).")
    return {"ok": True}


# ------------------------------------------------------------------ voice
@router.get("/voice/status")
async def voice_status(user: dict = Depends(current_user)):
    return voice.status()


@router.post("/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...), language: str | None = Form(None),
                           user: dict = Depends(chat_rate_limit)):
    data = await file.read(voice.MAX_BYTES + 1)
    try:
        text = await voice.transcribe(data, file.content_type or "", file.filename or "audio.webm", language)
    except voice.VoiceError as e:
        raise _bad(e)
    return {"text": text}
