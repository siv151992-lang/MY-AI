"""Unified task monitor: every long-running job (autonomous agent runs, Deep research, agent-team runs and automation
runs) in one list, with live status and cancel."""
from __future__ import annotations

from .. import db

TERMINAL = {"completed", "completed_with_issues", "failed", "cancelled", "succeeded"}

SOURCES = {
    # kind: (SQL selecting id, title, status, error, created_at, updated_at, finished_at for a user, url prefix)
    "agent": ("SELECT id, goal AS title, status, error, created_at, updated_at, finished_at FROM agent_runs "
              "WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2", "/agent/"),
    "research": ("SELECT id, question AS title, status, error, created_at, updated_at, finished_at FROM research_runs "
                 "WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2", "/research/"),
    "team": ("SELECT id, goal AS title, status, error, created_at, updated_at, finished_at FROM team_runs "
             "WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2", "/team/"),
    "automation": ("SELECT r.id, a.name AS title, r.status, r.error, r.created_at, r.updated_at, r.finished_at, "
                   "r.steps, r.automation_id FROM automation_runs r JOIN automations a ON a.id = r.automation_id "
                   "WHERE r.user_id=$1 ORDER BY r.created_at DESC LIMIT $2", "/automations/"),
}
LABELS = {"agent": "Autonomous agent", "research": "Deep research", "team": "Agent team", "automation": "Automation"}


def _progress(kind: str, r) -> dict | None:
    if kind == "automation":
        import json
        steps = r["steps"] if not isinstance(r["steps"], str) else json.loads(r["steps"])
        done = sum(1 for s in steps if s.get("status") in ("succeeded", "failed", "skipped", "cancelled"))
        running = next((s.get("title") for s in steps if s.get("status") == "running"), None)
        return {"done": done, "total": len(steps), "current": running}
    return None


async def snapshot(user_id: str, limit: int = 25) -> dict:
    items = []
    for kind, (sql, url) in SOURCES.items():
        for r in await db.pool().fetch(sql, user_id, limit):
            end = r["finished_at"] or (r["updated_at"] if r["status"] in TERMINAL else None)
            items.append({
                "kind": kind, "kind_label": LABELS[kind], "id": str(r["id"]), "title": (r["title"] or "")[:200],
                "status": r["status"], "active": r["status"] not in TERMINAL, "error": (r["error"] or "")[:300],
                "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
                "duration_s": int(((end or r["updated_at"]) - r["created_at"]).total_seconds()),
                "url": url + (str(r["automation_id"]) if kind == "automation" else str(r["id"])),
                "progress": _progress(kind, r)})
    items.sort(key=lambda x: (not x["active"], x["created_at"]), reverse=False)
    active = [i for i in items if i["active"]]
    recent = sorted([i for i in items if not i["active"]], key=lambda x: x["created_at"], reverse=True)[:limit]
    active.sort(key=lambda x: x["created_at"], reverse=True)
    counts = {}
    for i in items:
        counts.setdefault(i["kind"], {"active": 0, "failed": 0, "total": 0})
        c = counts[i["kind"]]
        c["total"] += 1
        c["active"] += i["active"]
        c["failed"] += i["status"] == "failed"
    return {"active": active, "recent": recent, "counts": counts}


async def cancel(user_id: str, kind: str, run_id: str) -> bool:
    if kind == "agent":
        from ..autonomy.manager import get_manager
        return await get_manager().cancel(user_id, run_id)
    if kind == "research":
        from ..research import store
        from ..research.manager import get_manager
        return bool(await store.get_run(user_id, run_id)) and get_manager().cancel(run_id)
    if kind == "team":
        from ..team import store
        from ..team.manager import get_manager
        return bool(await store.get_run(user_id, run_id)) and get_manager().cancel(run_id)
    if kind == "automation":
        from .automations import get_runner
        return get_runner().cancel(user_id, run_id)
    return False
