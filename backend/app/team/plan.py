"""Task graphs: parsing the Planning Agent's output, validating it, and scheduling it into waves."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..agent.jsonutil import extract_json

MAX_TASKS = 10


@dataclass
class Task:
    id: str                       # T1, T2…
    agent: str
    title: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = ""
    status: str = "pending"       # pending | running | done | failed | skipped
    attempts: int = 0
    result: str = ""
    sources: list[str] = field(default_factory=list)
    confidence: str = ""
    notes: str = ""
    error: str = ""
    tool_calls: int = 0
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class PlanError(ValueError):
    pass


def parse_plan(text: str, agents: set[str]) -> tuple[list[Task], list[str]]:
    """Returns (tasks, warnings). Unknown agents, unknown dependencies and cycles are repaired or rejected."""
    data = extract_json(text)
    items = data.get("tasks", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    tasks: list[Task] = []
    warnings: list[str] = []
    ids: dict[str, str] = {}
    for i, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        agent = str(it.get("agent", "")).strip().lower().replace(" agent", "").replace(" ", "_")
        if agent not in agents:
            warnings.append(f"Task '{it.get('title', i + 1)}' was assigned to unknown agent '{it.get('agent')}' and was dropped.")
            continue
        instr = " ".join(str(it.get("instruction") or it.get("task") or "").split())[:3000]
        if len(instr) < 5:
            continue
        tid = f"T{len(tasks) + 1}"
        ids[str(it.get("id") or f"T{i + 1}").strip()] = tid
        tasks.append(Task(tid, agent, " ".join(str(it.get("title") or instr[:60]).split())[:120], instr,
                          [str(d).strip() for d in it.get("depends_on") or []],
                          " ".join(str(it.get("expected_output") or "").split())[:500]))
        if len(tasks) >= MAX_TASKS:
            if len(items) > MAX_TASKS:
                warnings.append(f"The plan was capped at {MAX_TASKS} tasks.")
            break
    if not tasks:
        raise PlanError("The Planning Agent did not produce any usable tasks.")
    for t in tasks:  # remap dependency ids to the renumbered ones; drop unknown/self references
        deps = []
        for d in t.depends_on:
            nd = ids.get(d) or (d if re.fullmatch(r"T\d+", d) and any(x.id == d for x in tasks) else None)
            if nd and nd != t.id and nd not in deps:
                deps.append(nd)
            elif not nd:
                warnings.append(f"{t.id} depended on unknown task '{d}'; dependency removed.")
        t.depends_on = deps
    _break_cycles(tasks, warnings)
    return tasks, warnings


def _break_cycles(tasks: list[Task], warnings: list[str]) -> None:
    by_id = {t.id: t for t in tasks}
    state: dict[str, int] = {}

    def visit(t: Task) -> None:
        state[t.id] = 1
        for d in list(t.depends_on):
            if state.get(d) == 1:  # back edge: cycle
                t.depends_on.remove(d)
                warnings.append(f"Removed circular dependency {t.id} → {d}.")
            elif state.get(d) is None:
                visit(by_id[d])
        state[t.id] = 2

    for t in tasks:
        if t.id not in state:
            visit(t)


def waves(tasks: list[Task]) -> list[list[str]]:
    """Topological layers: every task in a wave depends only on earlier waves (for display and testing)."""
    done: set[str] = set()
    out = []
    remaining = {t.id: t for t in tasks}
    while remaining:
        layer = [tid for tid, t in remaining.items() if all(d in done for d in t.depends_on)]
        if not layer:
            raise PlanError("The task graph contains a cycle.")
        out.append(layer)
        done.update(layer)
        for tid in layer:
            remaining.pop(tid)
    return out


def ready(tasks: list[Task]) -> list[Task]:
    finished = {t.id for t in tasks if t.status in ("done", "failed", "skipped")}
    return [t for t in tasks if t.status == "pending" and all(d in finished for d in t.depends_on)]
