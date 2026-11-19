"""Project workspaces: group chats, documents, code workspaces, research, team runs, agent runs and automations under
one project with shared instructions. Chats in a project get the project's instructions, and memories learned there
stay with the project (see memory scoping)."""
from __future__ import annotations

from .. import db

# kind -> (table, title column, url prefix)
ITEM_KINDS = {
    "conversation": ("conversations", "title", "/chat/"),
    "document": ("files", "filename", "/documents/"),
    "workspace": ("workspaces", "name", "/code/"),
    "research": ("research_runs", "question", "/research/"),
    "team": ("team_runs", "goal", "/team/"),
    "agent_run": ("agent_runs", "goal", "/agent/"),
    "automation": ("automations", "name", "/automations/"),
}


async def list_projects(user_id: str, include_archived: bool = False) -> list[dict]:
    rows = await db.pool().fetch(
        "SELECT p.*, (SELECT count(*) FROM conversations c WHERE c.project_id = p.id) AS chats, "
        "(SELECT count(*) FROM files f WHERE f.project_id = p.id) AS documents "
        "FROM projects p WHERE p.user_id=$1 AND ($2 OR NOT p.archived) ORDER BY p.updated_at DESC", user_id, include_archived)
    return db.rows(rows)


async def get_project(user_id: str, project_id: str) -> dict | None:
    try:
        return db.row(await db.pool().fetchrow("SELECT * FROM projects WHERE id=$1 AND user_id=$2", project_id, user_id))
    except Exception:  # invalid uuid
        return None


async def create_project(user_id: str, name: str, description: str = "", instructions: str = "") -> dict:
    return db.row(await db.pool().fetchrow(
        "INSERT INTO projects (user_id, name, description, instructions) VALUES ($1,$2,$3,$4) RETURNING *",
        user_id, name.strip(), description.strip(), instructions.strip()))


async def update_project(user_id: str, project_id: str, **fields) -> dict | None:
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "instructions", "archived") and v is not None}
    if not allowed:
        return await get_project(user_id, project_id)
    sets = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(allowed))
    return db.row(await db.pool().fetchrow(
        f"UPDATE projects SET {sets}, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *",
        project_id, user_id, *allowed.values()))


async def delete_project(user_id: str, project_id: str) -> bool:
    """Deletes the project only; its chats, documents and runs are kept (they become unassigned)."""
    r = await db.pool().execute("DELETE FROM projects WHERE id=$1 AND user_id=$2", project_id, user_id)
    return r.endswith("1")


async def items(user_id: str, project_id: str) -> dict[str, list[dict]]:
    out = {}
    for kind, (table, title, url) in ITEM_KINDS.items():
        extra = ", status" if table.endswith("_runs") else ""
        rows = await db.pool().fetch(
            f"SELECT id, {title} AS title{extra}, created_at FROM {table} WHERE user_id=$1 AND project_id=$2 "
            f"ORDER BY created_at DESC LIMIT 100", user_id, project_id)
        out[kind] = [{**db.row(r), "url": url + str(r["id"])} for r in rows]
    return out


async def assign(user_id: str, kind: str, item_id: str, project_id: str | None) -> bool:
    """Move an item into a project (or out of it with project_id None). Both must belong to the user."""
    if kind not in ITEM_KINDS:
        raise ValueError(f"Unknown item kind '{kind}'")
    if project_id and not await get_project(user_id, project_id):
        raise ValueError("Project not found")
    table = ITEM_KINDS[kind][0]
    try:
        r = await db.pool().execute(f"UPDATE {table} SET project_id=$3 WHERE id=$1 AND user_id=$2",
                                    item_id, user_id, project_id)
    except Exception:  # invalid uuid
        return False
    if project_id:
        await db.pool().execute("UPDATE projects SET updated_at=now() WHERE id=$1", project_id)
    return r.endswith("1")


def section(project: dict) -> str:
    parts = [f"## Project: {project['name']}"]
    if project.get("description"):
        parts.append(project["description"])
    if project.get("instructions"):
        parts.append("Project instructions from the user (follow them in this chat):\n" + project["instructions"])
    return "\n\n".join(parts)
