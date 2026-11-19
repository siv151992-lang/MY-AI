"""Custom agents: user-defined assistants with their own instructions, tools and model.

Use them by chatting with them, by giving them a one-off task, as a step in an automation, or as members of the
multi-agent team (the planner sees them next to the built-in specialists)."""
from __future__ import annotations

import json
import re

from .. import db


class AgentError(ValueError):
    pass


def team_key(agent: dict) -> str:
    """Name the team planner uses for this agent (letters/digits/underscore, prefixed to avoid clashes)."""
    slug = re.sub(r"[^a-z0-9]+", "_", agent["name"].lower()).strip("_")[:24] or "agent"
    return f"custom_{slug}_{str(agent['id'])[:4]}"


def validate(data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    instructions = str(data.get("instructions", "")).strip()
    if not name or len(name) > 60:
        raise AgentError("Name must be 1-60 characters")
    if not instructions or len(instructions) > 20_000:
        raise AgentError("Instructions must be 1-20000 characters")
    return {"name": name, "description": str(data.get("description", "")).strip()[:300], "instructions": instructions,
            "tools": [str(t)[:64] for t in (data.get("tools") or [])][:25], "model_id": data.get("model_id") or None,
            "use_memory": bool(data.get("use_memory", True)), "in_team": bool(data.get("in_team", True))}


def _row(r) -> dict:
    d = db.row(r)
    if isinstance(d.get("tools"), str):
        d["tools"] = json.loads(d["tools"])
    d["team_key"] = team_key(d)
    return d


async def list_agents(user_id: str) -> list[dict]:
    return [_row(r) for r in await db.pool().fetch(
        "SELECT * FROM custom_agents WHERE user_id=$1 ORDER BY updated_at DESC", user_id)]


async def get_agent(user_id: str, agent_id: str) -> dict | None:
    try:
        r = await db.pool().fetchrow("SELECT * FROM custom_agents WHERE id=$1 AND user_id=$2", agent_id, user_id)
    except Exception:
        return None
    return _row(r) if r else None


async def create_agent(user_id: str, data: dict) -> dict:
    d = validate(data)
    return _row(await db.pool().fetchrow(
        "INSERT INTO custom_agents (user_id, name, description, instructions, tools, model_id, use_memory, in_team) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8) RETURNING *", user_id, d["name"], d["description"], d["instructions"],
        json.dumps(d["tools"]), d["model_id"], d["use_memory"], d["in_team"]))


async def update_agent(user_id: str, agent_id: str, data: dict) -> dict | None:
    d = validate(data)
    r = await db.pool().fetchrow(
        "UPDATE custom_agents SET name=$3, description=$4, instructions=$5, tools=$6::jsonb, model_id=$7, use_memory=$8, "
        "in_team=$9, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *", agent_id, user_id, d["name"],
        d["description"], d["instructions"], json.dumps(d["tools"]), d["model_id"], d["use_memory"], d["in_team"])
    return _row(r) if r else None


async def delete_agent(user_id: str, agent_id: str) -> bool:
    try:
        return (await db.pool().execute("DELETE FROM custom_agents WHERE id=$1 AND user_id=$2", agent_id, user_id)).endswith("1")
    except Exception:
        return False


async def run_task(user_id: str, agent: dict, task: str, *, emit=None) -> dict:
    from . import executor
    out, model = await executor.execute(user_id, label=f"{agent['name']} (a custom agent)", instructions=agent["instructions"],
                                        prompt=task, tool_names=agent.get("tools") or [], model_id=agent.get("model_id"),
                                        emit=emit)
    return {"status": out.status, "result": out.result, "sources": out.sources, "notes": out.notes, "error": out.error,
            "model": model, "tool_calls": out.tool_calls,
            "tools_used": [{"tool": t["tool"], "ok": t["ok"], "input": t["input"]} for t in out.tool_log]}


def chat_section(agent: dict) -> str:
    return (f"## You are acting as the user's custom agent \"{agent['name']}\"\n"
            + (agent["description"] + "\n\n" if agent.get("description") else "")
            + "Instructions written by the user for this agent (follow them):\n" + agent["instructions"])


# ------------------------------------------------------------------ team integration
class CustomTeamSpec:
    """Duck-types team.specialists.AgentSpec so the orchestrator can delegate to a custom agent."""

    def __init__(self, agent: dict):
        self.agent = agent
        self.name = team_key(agent)
        self.tools = list(agent.get("tools") or [])
        self.side_effects = "generate_document" in self.tools or any(t.startswith("api_") for t in self.tools)
        self.requires_tools = False
        self.label = f"{agent['name']} (custom agent)"
        self.description = agent.get("description") or agent["instructions"][:160]

    def system(self, control: str = "submit_result") -> str:
        from ..team.specialists import COMMON
        return COMMON.format(label=self.label, control=control) + "\n\nYour instructions from the user:\n" + self.agent["instructions"]


async def team_specs(user_id: str) -> dict[str, CustomTeamSpec]:
    try:
        agents = await list_agents(user_id)
    except Exception:  # noqa: BLE001 - no database in unit tests
        return {}
    return {s.name: s for s in (CustomTeamSpec(a) for a in agents if a["in_team"])}
