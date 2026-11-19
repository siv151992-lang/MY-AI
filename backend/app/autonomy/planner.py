"""Goal planning, re-planning after failures, and remediation after failed verification."""
from __future__ import annotations

import json

from ..agent.jsonutil import extract_json
from ..llm.formats import text_msg
from .models import Step

PLAN_SYSTEM = """You are the planning module of an autonomous agent that performs REAL actions through tools.
Turn the user's goal into an ordered plan of concrete steps. Each step:
  "title": short imperative,
  "description": exactly what to do and with which inputs,
  "tool": the main tool the step needs, chosen ONLY from the available tools list, or "none" for pure \
reasoning/writing steps,
  "success_criteria": an observable, checkable outcome (e.g. "HTTP 201 returned", "file report.md created").
Rules:
- Use only tools that exist. If the goal needs a capability no tool provides, add a final step with tool "none" that \
explains to the user what is missing — never plan to fake it.
- Gather information before acting; put irreversible or external actions (emails, POST requests, messages) as late as \
possible and only once their content is ready.
- Deliverables the user should keep go into files via create_file when that tool exists.
- 2-{max_steps} steps. Return JSON only: {{"steps": [...]}}"""

REPLAN_SYSTEM = """You are the recovery module of an autonomous agent. A step failed. Given the goal, the steps \
completed so far (with results), the failed step and its errors, write the REMAINING steps needed to still achieve \
the goal: try an alternative approach or tool, work around the failure, or — if the goal cannot be achieved — a \
single step with tool "none" that reports clearly what is blocked and why.
Same step format and tool rules as planning. Return JSON only: {"steps": [...]}"""

REMEDIATE_SYSTEM = """You are the remediation module of an autonomous agent. The run finished but verification found \
the goal is not fully achieved. Given the goal, executed steps and the verifier's issues, write the minimal extra \
steps that would close the gaps (same step format and tool rules). If nothing more can be done with the available \
tools, return {"steps": []}. Return JSON only: {"steps": [...]}"""


def parse_steps(text: str, tool_names: list[str], start_idx: int, origin: str, max_steps: int) -> list[Step]:
    data = extract_json(text)
    raw = data.get("steps") if isinstance(data, dict) else data if isinstance(data, list) else []
    steps: list[Step] = []
    for s in raw or []:
        if isinstance(s, str):
            s = {"title": s}
        if not isinstance(s, dict) or not str(s.get("title", "")).strip():
            continue
        tool = str(s.get("tool") or "none").strip()
        if tool not in tool_names:
            tool = "none"  # never trust a tool the system doesn't have
        steps.append(Step(idx=start_idx + len(steps), title=str(s["title"]).strip()[:200],
                          description=str(s.get("description") or "").strip()[:2000], tool=tool,
                          success_criteria=str(s.get("success_criteria") or "").strip()[:500], origin=origin))
        if len(steps) >= max_steps:
            break
    return steps


def _catalog_text(catalog: list[dict]) -> str:
    return "\n".join(f"- {t['name']}{' (requires user approval)' if t['needs_approval'] else ''}: {t['description']}"
                     for t in catalog) or "(no tools)"


def _history(steps: list[Step]) -> str:
    return "\n".join(f"{s.idx + 1}. [{s.status}] {s.title} (tool: {s.tool})"
                     + (f"\n   result: {s.result[:600]}" if s.result else "")
                     + (f"\n   error: {s.error[:400]}" if s.error else "") for s in steps)


class GoalPlanner:
    def __init__(self, llm, model: str, max_steps: int = 12):
        self.llm, self.model, self.max_steps = llm, model, max_steps

    async def _ask(self, system: str, prompt: str) -> str:
        return await self.llm.complete(model=self.model, system=system, max_tokens=1800,
                                       messages=[text_msg("user", prompt)])

    async def plan(self, goal: str, catalog: list[dict], context: str = "") -> list[Step]:
        names = [t["name"] for t in catalog]
        prompt = f"<goal>\n{goal}\n</goal>\n\n<available_tools>\n{_catalog_text(catalog)}\n</available_tools>"
        if context:
            prompt += f"\n\n<context>\n{context}\n</context>"
        reply = await self._ask(PLAN_SYSTEM.format(max_steps=self.max_steps), prompt)
        steps = parse_steps(reply, names, 0, "plan", self.max_steps)
        return steps or [Step(idx=0, title="Work on the goal", description=goal, tool="none")]

    async def replan(self, goal: str, catalog: list[dict], done: list[Step], failed: Step) -> list[Step]:
        prompt = (f"<goal>\n{goal}\n</goal>\n\n<available_tools>\n{_catalog_text(catalog)}\n</available_tools>\n\n"
                  f"<steps_so_far>\n{_history(done)}\n</steps_so_far>\n\n<failed_step>\n{_history([failed])}\n</failed_step>")
        reply = await self._ask(REPLAN_SYSTEM, prompt)
        return parse_steps(reply, [t["name"] for t in catalog], failed.idx + 1, "replan", 6)

    async def remediate(self, goal: str, catalog: list[dict], steps: list[Step], issues: list[str]) -> list[Step]:
        prompt = (f"<goal>\n{goal}\n</goal>\n\n<available_tools>\n{_catalog_text(catalog)}\n</available_tools>\n\n"
                  f"<executed_steps>\n{_history(steps)}\n</executed_steps>\n\n<issues>\n{json.dumps(issues)}\n</issues>")
        reply = await self._ask(REMEDIATE_SYSTEM, prompt)
        return parse_steps(reply, [t["name"] for t in catalog], len(steps), "remediation", 4)
