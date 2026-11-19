"""Runs one self-contained AI task (a skill, a custom agent's task, an automation step) with a chosen set of tools.

It reuses the team's grounded tool loop: the model must finish with a structured submission, sources are kept only
when they appear in tool results, and every tool call is logged."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..agent.tools.base import Tool, ToolContext, ToolRegistry

BASE = """You are {label}. Do the task you are given well and nothing else.
- Work only from the inputs you were given and evidence you actually obtain with your tools. Never claim you read, \
ran, checked or found something unless a tool result in this conversation shows it.
- If you cannot complete the task, say exactly what is missing; an honest partial result beats a confident guess.
- Finish by calling {control} exactly once, with your full result in Markdown.

{instructions}"""


@dataclass
class TaskSpec:
    """Duck-types team.specialists.AgentSpec for run_specialist."""
    name: str
    label: str
    instructions: str
    requires_tools: bool = False

    def system(self, control: str = "submit_result") -> str:
        return BASE.format(label=self.label, control=control, instructions=self.instructions.strip())


async def tool_pool(user_id: str) -> dict[str, Tool]:
    """Every tool this user can use right now: built-in tools that are configured plus their API integrations."""
    from ..team.manager import default_pool
    from . import integrations
    pool = dict(default_pool())
    for t in await integrations.tools_for_user(user_id, allow_writes=False):
        pool[t.name] = t
    return pool


async def tool_catalog(user_id: str) -> list[dict]:
    pool = await tool_pool(user_id)
    return [{"name": t.name, "description": t.description.split(". ")[0][:200]} for t in pool.values()]


def pick_model(model_id: str | None, prompt: str, user_routing: dict | None = None):
    """Resolve a model id ('auto'/None → adaptive router) to (provider, ModelSpec)."""
    from ..llm import registry
    from . import routing
    if not model_id or model_id == "auto":
        r = routing.route(prompt, registry.available_models(), prefs=user_routing)
        model_id = r.model_id
    spec = registry.get_model(model_id)
    return registry.provider_for(spec), spec


Emit = Callable[[str, dict], Awaitable[None]]


async def _noop(_t: str, _d: dict) -> None:
    return None


async def execute(user_id: str, *, label: str, instructions: str, prompt: str, tool_names: list[str],
                  model_id: str | None, emit: Emit | None = None, max_calls: int = 10,
                  extra_tools: list[Tool] | None = None, conversation_id: str = "task"):
    from ..team.loop import run_specialist
    from . import routing
    prefs = (await routing.get_prefs(user_id))["routing"]
    llm, spec = pick_model(model_id, prompt, prefs)
    pool = await tool_pool(user_id)
    for t in extra_tools or []:
        pool[t.name] = t
    reg = ToolRegistry([pool[n] for n in tool_names if n in pool])
    missing = [n for n in tool_names if n not in pool]
    ts = TaskSpec("task", label, instructions)
    out = await run_specialist(ts, llm, spec.model, prompt, reg, ToolContext(user_id=user_id, conversation_id=conversation_id),
                               emit or _noop, max_calls=max_calls, max_tokens=min(spec.max_tokens, 8192),
                               reasoning={"mode": spec.reasoning, "effort": "low"})
    if missing:
        out.notes = (out.notes + " " if out.notes else "") + f"[Unavailable tools skipped: {', '.join(missing)}]"
    return out, spec.id
