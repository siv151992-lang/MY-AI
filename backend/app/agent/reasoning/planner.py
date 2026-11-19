"""Planner: turns an analysis into ordered subtasks that together cover every part of the request."""
from __future__ import annotations

import json
import re

from ...llm.formats import text_msg
from ..jsonutil import extract_json
from .types import Plan, Subtask, TaskAnalysis

PLANNER_SYSTEM = """You are the planning module of an AI assistant. Given an analysis of the user's request, \
produce an ordered plan of 2-7 concrete subtasks the assistant will execute. Each subtask has:
  "goal": what to do (imperative, specific),
  "method": one of "reason", "web_search", "fetch_url", "run_python", "search_documents",
  "covers": indices (0-based) of the request parts it helps answer,
  "done_when": a short, checkable completion criterion.
Every part must be covered by at least one subtask. Gather information and compute before concluding. \
The last subtask should assemble and check the final answer.
Return JSON only: {"subtasks": [ {...}, ... ]}"""


def needs_plan(analysis: TaskAnalysis, agent_mode: bool) -> bool:
    if agent_mode and not analysis.fast_path:
        return True
    return analysis.complexity == "complex" or (analysis.complexity == "moderate" and len(analysis.parts) >= 3)


def parse_plan(text: str, n_parts: int) -> Plan:
    data = extract_json(text)
    raw = data.get("subtasks") if isinstance(data, dict) else data if isinstance(data, list) else None
    subtasks: list[Subtask] = []
    if isinstance(raw, list):
        for i, s in enumerate(raw[:7], 1):
            if isinstance(s, str):
                subtasks.append(Subtask(id=i, goal=s.strip()))
                continue
            if not isinstance(s, dict) or not str(s.get("goal", "")).strip():
                continue
            covers = [int(c) for c in s.get("covers", []) if isinstance(c, (int, float)) and 0 <= int(c) < n_parts] \
                if isinstance(s.get("covers"), list) else []
            subtasks.append(Subtask(id=len(subtasks) + 1, goal=str(s["goal"]).strip(),
                                    method=str(s.get("method") or "reason"), covers=covers,
                                    done_when=str(s.get("done_when") or "").strip()))
    if not subtasks:  # numbered-list fallback
        for ln in text.splitlines():
            m = re.match(r"^\s*(\d+[.)]|[-*])\s+(.*)", ln)
            if m and m.group(2).strip():
                subtasks.append(Subtask(id=len(subtasks) + 1, goal=m.group(2).strip()))
    return Plan(subtasks=subtasks[:7])


def ensure_coverage(plan: Plan, analysis: TaskAnalysis) -> Plan:
    """Guarantee every part is addressed: add a subtask for any part the plan forgot."""
    if not analysis.parts:
        return plan
    covered = {c for s in plan.subtasks for c in s.covers}
    # plans without coverage info are trusted as-is unless empty
    if plan.subtasks and not covered:
        return plan
    missing = [i for i in range(len(analysis.parts)) if i not in covered]
    for i in missing:
        plan.subtasks.insert(max(len(plan.subtasks) - 1, 0),
                             Subtask(id=0, goal=f"Answer: {analysis.parts[i]}", method="reason", covers=[i]))
    if not plan.subtasks:
        plan.subtasks = [Subtask(id=0, goal=f"Answer: {p}", covers=[i]) for i, p in enumerate(analysis.parts)]
    for n, s in enumerate(plan.subtasks, 1):
        s.id = n
    return plan


class LLMPlanner:
    def __init__(self, llm, model: str):
        self.llm, self.model = llm, model

    async def plan(self, analysis: TaskAnalysis, tool_names: list[str]) -> Plan:
        payload = {"goal": analysis.goal, "parts": analysis.parts, "task_types": analysis.task_types,
                   "assumptions": analysis.assumptions, "suggested_tools": analysis.needs_tools,
                   "available_tools": tool_names}
        reply = await self.llm.complete(model=self.model, system=PLANNER_SYSTEM, max_tokens=900,
                                        messages=[text_msg("user", json.dumps(payload, ensure_ascii=False))])
        return ensure_coverage(parse_plan(reply, len(analysis.parts)), analysis)
