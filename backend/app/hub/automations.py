"""User-defined automations: multi-step AI workflows started manually, on a schedule, or by a webhook.

A workflow is a list of steps. Each step's output is available to later steps as {{prev}} (previous step) and
{{steps.N}} (step N, 1-based); a webhook's JSON body is {{payload}}; {{date}} and {{now}} give the current time.

Step types
  prompt    an AI task with instructions, optional tools and model           (always available)
  skill     run a saved or built-in skill with templated inputs              (always available)
  agent     give one of your custom agents a task                             (always available)
  research  run Deep research and wait for the report                         (needs a web search key)
  team      run the multi-agent team and wait for the result                  (always available)
  http      call one of your API integrations (writes only if allowed there)  (needs an integration)
  notify    send the result to Slack or by email                              (needs SLACK_WEBHOOK_URL / SMTP)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timezone

from .. import db
from .cron import CronError, Schedule

log = logging.getLogger("aria.automations")

STEP_TYPES = ("prompt", "skill", "agent", "research", "team", "http", "notify")
TRIGGERS = ("manual", "schedule", "webhook")
MAX_STEPS = 10
STEP_TIMEOUT_S = 30 * 60
VAR = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


class AutomationError(ValueError):
    pass


# ------------------------------------------------------------------ validation
def validate(data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 80:
        raise AutomationError("Name must be 1-80 characters")
    trigger = data.get("trigger_type") or "manual"
    if trigger not in TRIGGERS:
        raise AutomationError(f"trigger_type must be one of {', '.join(TRIGGERS)}")
    schedule = str(data.get("schedule") or "").strip()
    tz = str(data.get("timezone") or "UTC").strip()
    if trigger == "schedule":
        try:
            Schedule(schedule, tz)
        except CronError as e:
            raise AutomationError(str(e))
    steps = data.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise AutomationError("Add at least one step")
    if len(steps) > MAX_STEPS:
        raise AutomationError(f"At most {MAX_STEPS} steps")
    clean = []
    for i, st in enumerate(steps, 1):
        t = st.get("type")
        if t not in STEP_TYPES:
            raise AutomationError(f"Step {i}: unknown type '{t}'")
        need = {"prompt": ["instructions"], "skill": ["skill_id"], "agent": ["agent_id", "task"], "research": ["question"],
                "team": ["goal"], "http": ["integration_id", "path"], "notify": ["channel", "message"]}[t]
        for k in need:
            if not str(st.get(k) or "").strip():
                raise AutomationError(f"Step {i} ({t}): '{k}' is required")
        if t == "notify" and st.get("channel") not in ("slack", "email"):
            raise AutomationError(f"Step {i}: channel must be slack or email")
        if t == "notify" and st["channel"] == "email" and not st.get("to"):
            raise AutomationError(f"Step {i}: give the email recipient(s)")
        clean.append({k: v for k, v in st.items() if k in (
            "type", "title", "instructions", "tools", "model_id", "skill_id", "inputs", "agent_id", "task", "question",
            "depth", "goal", "integration_id", "method", "path", "query", "body", "channel", "to", "subject", "message",
            "continue_on_error")})
    return {"name": name, "trigger_type": trigger, "schedule": schedule if trigger == "schedule" else "", "timezone": tz,
            "steps": clean, "enabled": bool(data.get("enabled", True)), "project_id": data.get("project_id") or None}


def _next_run(d: dict) -> datetime | None:
    if d["trigger_type"] != "schedule" or not d["enabled"]:
        return None
    return Schedule(d["schedule"], d["timezone"]).next_after(datetime.now(timezone.utc))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ store
def _row(r) -> dict:
    d = db.row(r)
    for k in ("steps", "notify"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["has_webhook"] = bool(d.pop("webhook_hash", None))
    if d.get("trigger_type") == "schedule" and d.get("schedule"):
        try:
            d["schedule_text"] = Schedule(d["schedule"], d.get("timezone") or "UTC").describe()
        except CronError:
            d["schedule_text"] = d["schedule"]
    return d


async def list_automations(user_id: str) -> list[dict]:
    rows = await db.pool().fetch(
        "SELECT a.*, (SELECT status FROM automation_runs r WHERE r.automation_id=a.id ORDER BY created_at DESC LIMIT 1) "
        "AS last_status FROM automations a WHERE user_id=$1 ORDER BY updated_at DESC", user_id)
    return [_row(r) for r in rows]


async def get_automation(user_id: str, aid: str) -> dict | None:
    try:
        r = await db.pool().fetchrow("SELECT * FROM automations WHERE id=$1 AND user_id=$2", aid, user_id)
    except Exception:
        return None
    return _row(r) if r else None


async def create_automation(user_id: str, data: dict) -> tuple[dict, str | None]:
    d = validate(data)
    token = secrets.token_urlsafe(24) if d["trigger_type"] == "webhook" else None
    r = await db.pool().fetchrow(
        "INSERT INTO automations (user_id, project_id, name, enabled, trigger_type, schedule, timezone, webhook_hash, "
        "steps, next_run_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10) RETURNING *", user_id, d["project_id"],
        d["name"], d["enabled"], d["trigger_type"], d["schedule"], d["timezone"], _hash(token) if token else None,
        json.dumps(d["steps"]), _next_run(d))
    return _row(r), token


async def update_automation(user_id: str, aid: str, data: dict) -> tuple[dict | None, str | None]:
    cur = await db.pool().fetchrow("SELECT trigger_type, webhook_hash FROM automations WHERE id=$1 AND user_id=$2", aid, user_id)
    if not cur:
        return None, None
    d = validate(data)
    token = None
    wh = cur["webhook_hash"]
    if d["trigger_type"] == "webhook" and not wh:
        token = secrets.token_urlsafe(24)
        wh = _hash(token)
    elif d["trigger_type"] != "webhook":
        wh = None
    r = await db.pool().fetchrow(
        "UPDATE automations SET project_id=$3, name=$4, enabled=$5, trigger_type=$6, schedule=$7, timezone=$8, "
        "webhook_hash=$9, steps=$10::jsonb, next_run_at=$11, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *",
        aid, user_id, d["project_id"], d["name"], d["enabled"], d["trigger_type"], d["schedule"], d["timezone"], wh,
        json.dumps(d["steps"]), _next_run(d))
    return _row(r), token


async def rotate_webhook(user_id: str, aid: str) -> str | None:
    token = secrets.token_urlsafe(24)
    r = await db.pool().execute("UPDATE automations SET webhook_hash=$3 WHERE id=$1 AND user_id=$2 AND trigger_type='webhook'",
                                aid, user_id, _hash(token))
    return token if r.endswith("1") else None


async def delete_automation(user_id: str, aid: str) -> bool:
    try:
        return (await db.pool().execute("DELETE FROM automations WHERE id=$1 AND user_id=$2", aid, user_id)).endswith("1")
    except Exception:
        return False


async def list_runs(user_id: str, aid: str | None = None, limit: int = 50) -> list[dict]:
    rows = await db.pool().fetch(
        "SELECT r.*, a.name AS automation_name FROM automation_runs r JOIN automations a ON a.id = r.automation_id "
        "WHERE r.user_id=$1 AND ($2::uuid IS NULL OR r.automation_id=$2) ORDER BY r.created_at DESC LIMIT $3",
        user_id, aid, limit)
    return [_run_row(r) for r in rows]


async def get_run(user_id: str, run_id: str) -> dict | None:
    try:
        r = await db.pool().fetchrow("SELECT r.*, a.name AS automation_name FROM automation_runs r JOIN automations a "
                                     "ON a.id=r.automation_id WHERE r.id=$1 AND r.user_id=$2", run_id, user_id)
    except Exception:
        return None
    return _run_row(r) if r else None


def _run_row(r) -> dict:
    d = db.row(r)
    if isinstance(d.get("steps"), str):
        d["steps"] = json.loads(d["steps"])
    return d


async def check_webhook(aid: str, token: str) -> dict | None:
    try:
        r = await db.pool().fetchrow("SELECT * FROM automations WHERE id=$1 AND trigger_type='webhook'", aid)
    except Exception:
        return None
    if not r or not r["webhook_hash"] or not r["enabled"]:
        return None
    if not hmac.compare_digest(r["webhook_hash"], _hash(token)):
        return None
    return dict(r)


# ------------------------------------------------------------------ templating
def render(text: str, env: dict) -> str:
    def val(m):
        key = m.group(1)
        if key.startswith("steps."):
            try:
                return env["steps"][int(key.split(".")[1]) - 1]
            except (ValueError, IndexError):
                return ""
        if key.startswith("payload.") and isinstance(env.get("payload_obj"), dict):
            cur = env["payload_obj"]
            for part in key.split(".")[1:]:
                cur = cur.get(part) if isinstance(cur, dict) else None
            return "" if cur is None else (cur if isinstance(cur, str) else json.dumps(cur))
        v = env.get(key, "")
        return v if isinstance(v, str) else json.dumps(v)
    return VAR.sub(val, str(text or ""))


# ------------------------------------------------------------------ execution
class Runner:
    """Runs automation workflows as background tasks and keeps track of them for cancelling."""

    def __init__(self, max_per_user: int = 3):
        self.tasks: dict[str, asyncio.Task] = {}
        self.owners: dict[str, str] = {}
        self.max_per_user = max_per_user
        self.loop_task: asyncio.Task | None = None

    def active(self, user_id: str) -> int:
        return sum(1 for r, t in self.tasks.items() if self.owners.get(r) == user_id and not t.done())

    async def start(self, automation: dict, trigger: str, payload=None) -> dict:
        uid = str(automation["user_id"])
        if self.active(uid) >= self.max_per_user:
            raise AutomationError(f"You already have {self.max_per_user} automation runs in progress.")
        steps = automation["steps"] if isinstance(automation["steps"], list) else json.loads(automation["steps"])
        plan = [{"index": i + 1, "type": s["type"], "title": s.get("title") or s["type"], "status": "pending"}
                for i, s in enumerate(steps)]
        run = _run_row(await db.pool().fetchrow(
            "INSERT INTO automation_runs (automation_id, user_id, trigger, steps) VALUES ($1,$2,$3,$4::jsonb) RETURNING *",
            automation["id"], uid, trigger, json.dumps(plan)))
        await db.pool().execute("UPDATE automations SET last_run_at=now() WHERE id=$1", automation["id"])
        from . import usage
        with usage.scope(uid, "automation"):
            task = asyncio.create_task(self._run(run["id"], uid, steps, plan, payload))
        self.tasks[str(run["id"])], self.owners[str(run["id"])] = task, uid
        task.add_done_callback(lambda _t, rid=str(run["id"]): self.tasks.pop(rid, None))
        return run

    def cancel(self, user_id: str, run_id: str) -> bool:
        t = self.tasks.get(run_id)
        if not t or t.done() or self.owners.get(run_id) != user_id:
            return False
        t.cancel()
        return True

    async def _save(self, run_id, plan, **fields) -> None:
        sets, args = ["steps=$2::jsonb", "updated_at=now()"], [run_id, json.dumps(plan, default=str)]
        for k, v in fields.items():
            args.append(v)
            sets.append(f"{k}=${len(args)}")
        await db.pool().execute(f"UPDATE automation_runs SET {', '.join(sets)} WHERE id=$1", *args)

    async def _run(self, run_id, user_id: str, steps: list[dict], plan: list[dict], payload) -> None:
        now = datetime.now(timezone.utc)
        env = {"prev": "", "steps": [], "now": now.isoformat(timespec="minutes"), "date": now.date().isoformat(),
               "payload": json.dumps(payload)[:20_000] if payload is not None else "", "payload_obj": payload}
        status, error = "succeeded", ""
        try:
            for i, step in enumerate(steps):
                plan[i].update(status="running", started_at=datetime.now(timezone.utc).isoformat())
                await self._save(run_id, plan)
                try:
                    out = await asyncio.wait_for(run_step(user_id, step, env), timeout=STEP_TIMEOUT_S)
                    plan[i].update(status="succeeded", output=out[:20_000])
                except asyncio.TimeoutError:
                    out = ""
                    plan[i].update(status="failed", error=f"Timed out after {STEP_TIMEOUT_S // 60} minutes")
                except (AutomationError, RuntimeError) as e:
                    out = ""
                    plan[i].update(status="failed", error=str(e)[:1000])
                except Exception as e:  # noqa: BLE001
                    log.exception("automation step crashed")
                    out = ""
                    plan[i].update(status="failed", error=f"Internal error: {type(e).__name__}")
                plan[i]["finished_at"] = datetime.now(timezone.utc).isoformat()
                env["steps"].append(out)
                if plan[i]["status"] == "failed":
                    if step.get("continue_on_error"):
                        env["prev"] = f"[step {i + 1} failed: {plan[i]['error']}]"
                        continue
                    status, error = "failed", f"Step {i + 1} ({plan[i]['title']}) failed: {plan[i]['error']}"
                    for rest in plan[i + 1:]:
                        rest["status"] = "skipped"
                    break
                env["prev"] = out
                await self._save(run_id, plan)
        except asyncio.CancelledError:
            status, error = "cancelled", "Cancelled by you."
            for p in plan:
                if p["status"] in ("pending", "running"):
                    p["status"] = "cancelled"
        output = env["prev"] if status == "succeeded" else ""
        await self._save(run_id, plan, status=status, error=error, output=output[:50_000],
                         finished_at=datetime.now(timezone.utc))

    # -------------------------------------------------------------- scheduler
    async def tick(self) -> int:
        """Start every scheduled automation that is due. Safe with several backend processes (row locks)."""
        started = 0
        async with db.pool().acquire() as c, c.transaction():
            due = await c.fetch("SELECT * FROM automations WHERE enabled AND trigger_type='schedule' AND next_run_at <= now() "
                                "ORDER BY next_run_at LIMIT 20 FOR UPDATE SKIP LOCKED")
            for a in due:
                try:
                    nxt = Schedule(a["schedule"], a["timezone"]).next_after(datetime.now(timezone.utc))
                except CronError:
                    nxt = None
                await c.execute("UPDATE automations SET next_run_at=$2 WHERE id=$1", a["id"], nxt)
        for a in due:
            try:
                await self.start(dict(a), "schedule")
                started += 1
            except AutomationError as e:
                log.warning("scheduled automation %s skipped: %s", a["id"], e)
        return started

    async def loop(self, interval: float = 20.0) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("automation scheduler tick failed")
            await asyncio.sleep(interval)


async def _wait(is_active, run_id: str, timeout: float = STEP_TIMEOUT_S) -> None:
    for _ in range(int(timeout / 2)):
        if not is_active(run_id):
            return
        await asyncio.sleep(2)


async def run_step(user_id: str, step: dict, env: dict) -> str:
    from . import custom_agents, integrations, skills
    t = step["type"]
    if t == "prompt":
        from . import executor
        out, _ = await executor.execute(user_id, label="an automation step", instructions="Follow the instructions exactly.",
                                        prompt=render(step["instructions"], env), tool_names=step.get("tools") or [],
                                        model_id=step.get("model_id"))
        return _outcome(out.status, out.result, out.error or out.notes)
    if t == "skill":
        sk = await skills.get_skill(user_id, step["skill_id"])
        if not sk:
            raise AutomationError("The skill used by this step no longer exists")
        values = {k: render(v, env) for k, v in (step.get("inputs") or {}).items()}
        values.setdefault("input", env["prev"])
        try:
            res = await skills.run(user_id, sk, values)
        except skills.SkillError as e:
            raise AutomationError(str(e))
        return _outcome(res["status"], res["result"], res["error"] or res["notes"])
    if t == "agent":
        ag = await custom_agents.get_agent(user_id, step["agent_id"])
        if not ag:
            raise AutomationError("The custom agent used by this step no longer exists")
        res = await custom_agents.run_task(user_id, ag, render(step["task"], env))
        return _outcome(res["status"], res["result"], res["error"] or res["notes"])
    if t == "research":
        from ..research import store as rstore
        from ..research.manager import ResearchUnavailable, get_manager
        try:
            run = await get_manager().start(user_id, render(step["question"], env), step.get("depth") or "standard", step.get("model_id"))
        except ResearchUnavailable as e:
            raise AutomationError(f"Deep research is not available: {e}")
        await _wait(get_manager().is_active, run["id"])
        done = await rstore.get_run(user_id, run["id"])
        if not done or done["status"] != "completed":
            raise AutomationError(f"Research run ended with status {done['status'] if done else 'unknown'}: {(done or {}).get('error', '')}")
        return done.get("report") or done.get("summary") or ""
    if t == "team":
        from ..team import store as tstore
        from ..team.manager import get_manager
        run = await get_manager().start(user_id, render(step["goal"], env), step.get("model_id"))
        await _wait(get_manager().is_active, run["id"])
        done = await tstore.get_run(user_id, run["id"])
        if not done or done["status"] not in ("completed", "completed_with_issues"):
            raise AutomationError(f"Team run ended with status {done['status'] if done else 'unknown'}: {(done or {}).get('error', '')}")
        return done.get("final") or ""
    if t == "http":
        row = await integrations._get_raw(user_id, step["integration_id"])
        if not row:
            raise AutomationError("The integration used by this step no longer exists")
        body = step.get("body")
        if isinstance(body, str) and body.strip():
            rendered = render(body, env)
            try:
                body = json.loads(rendered)
            except json.JSONDecodeError:
                raise AutomationError("The request body is not valid JSON after filling in the variables")
        query = {k: render(v, env) for k, v in (step.get("query") or {}).items()}
        try:
            res = await integrations.call(row, step.get("method") or "GET", render(step["path"], env), query=query,
                                          body=body or None, allow_writes=True)
        except integrations.IntegrationError as e:
            raise AutomationError(str(e))
        if not res["ok"]:
            raise AutomationError(f"HTTP {res['status']}: {res['body'][:500]}")
        return res["body"]
    if t == "notify":
        return await _notify(user_id, step, env)
    raise AutomationError(f"Unknown step type {t}")


def _outcome(status: str, result: str, problem: str) -> str:
    if status == "failed":
        raise AutomationError(problem or "The AI step reported failure")
    return result


async def _notify(user_id: str, step: dict, env: dict) -> str:
    from ..agent.tools.actions import make_email_tool, make_slack_tool
    from ..agent.tools.base import ToolContext
    from ..config import get_settings
    s = get_settings()
    msg = render(step["message"], env)
    ctx = ToolContext(user_id=user_id, conversation_id="automation")
    if step["channel"] == "slack":
        if not s.slack_webhook_url:
            raise AutomationError("Slack isn't configured on this server (set SLACK_WEBHOOK_URL in .env)")
        out = await make_slack_tool(s.slack_webhook_url).run({"text": msg}, ctx)
        if "responded 200" not in out:
            raise AutomationError(out)
        return msg
    if not (s.smtp_host and s.smtp_from):
        raise AutomationError("Email isn't configured on this server (set SMTP_HOST and SMTP_FROM in .env)")
    to = [a.strip() for a in re.split(r"[,;\s]+", str(step.get("to") or "")) if "@" in a][:10]
    tool = make_email_tool(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from)
    await tool.run({"to": to, "subject": render(step.get("subject") or "Aria automation", env)[:200], "body": msg}, ctx)
    return msg


_runner: Runner | None = None


def get_runner() -> Runner:
    global _runner
    if _runner is None:
        _runner = Runner()
    return _runner
