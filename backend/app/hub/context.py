"""Per-turn platform context for a chat message: project instructions, custom agent persona, skill, the user's API
integrations as tools, and the resulting model/tool restrictions."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..agent.tools.base import Tool, ToolRegistry


@dataclass
class HubContext:
    sections: list[str] = field(default_factory=list)
    model_id: str | None = None
    force_model: bool = False
    allowed_tools: list[str] | None = None      # None = no restriction
    extra_tools: list[Tool] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    project_id: str | None = None

    def filter_tools(self, tools: ToolRegistry) -> ToolRegistry:
        for t in self.extra_tools:
            if tools.get(t.name) is None:
                tools.add(t)
        if self.allowed_tools is None:
            return tools
        return ToolRegistry([tools.get(n) for n in self.allowed_tools if tools.get(n) is not None])


SKILL_IN_CHAT = "Placeholder {{input}} refers to the user's message in this turn."


async def for_turn(user_id: str, conv: dict, skill_id: str | None = None) -> HubContext:
    from . import custom_agents, integrations, projects, skills
    hub = HubContext()
    try:
        hub.extra_tools = await integrations.tools_for_user(user_id, allow_writes=False)
    except Exception:  # noqa: BLE001
        hub.extra_tools = []
    if conv.get("project_id"):
        p = await projects.get_project(user_id, str(conv["project_id"]))
        if p:
            hub.sections.append(projects.section(p))
            hub.project_id = str(p["id"])
            hub.meta["project_id"] = hub.project_id
    if conv.get("agent_id"):
        a = await custom_agents.get_agent(user_id, str(conv["agent_id"]))
        if a:
            hub.sections.append(custom_agents.chat_section(a))
            hub.allowed_tools = list(a.get("tools") or [])
            if a.get("model_id"):
                hub.model_id, hub.force_model = a["model_id"], True
            hub.meta["agent"] = {"id": str(a["id"]), "name": a["name"]}
    if skill_id:
        sk = await skills.get_skill(user_id, skill_id)
        if not sk:
            hub.errors.append("That skill no longer exists; answering normally.")
        else:
            instructions = skills.VAR.sub(lambda m: "the user's message" if m.group(1) == "input" else f"[{m.group(1)}]",
                                          sk["instructions"])
            hub.sections.append(f"## Skill: {sk['name']}\nApply this skill to the user's message in this turn:\n{instructions}")
            if sk.get("tools"):
                hub.allowed_tools = list(sk["tools"])
            if sk.get("model_id") and not hub.force_model:
                hub.model_id = sk["model_id"]
            hub.meta["skill"] = {"id": str(sk["id"]), "name": sk["name"]}
            await skills.count_use(sk)
    return hub
