"""Data structures shared by the reasoning components (pure Python, provider-agnostic)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

TaskType = Literal["math", "logic", "analytical", "technical", "factual", "creative", "conversational"]
Complexity = Literal["simple", "moderate", "complex"]
Effort = Literal["none", "low", "medium", "high"]
VerifyLevel = Literal["none", "light", "strict"]

TASK_TYPES: tuple[str, ...] = ("math", "logic", "analytical", "technical", "factual", "creative", "conversational")


@dataclass
class Ambiguity:
    is_ambiguous: bool = False
    interpretations: list[str] = field(default_factory=list)
    chosen: str | None = None          # the interpretation we will go with if we don't ask
    ask: bool = False                  # analyzer recommends asking before answering
    question: str | None = None        # the clarifying question to ask


@dataclass
class TaskAnalysis:
    """What the user wants, decomposed. Produced before any answering happens."""
    task_types: list[str] = field(default_factory=lambda: ["conversational"])
    complexity: str = "simple"
    goal: str = ""
    parts: list[str] = field(default_factory=list)          # every distinct sub-question that must be answered
    missing_info: list[str] = field(default_factory=list)   # facts we lack and cannot reasonably assume
    assumptions: list[str] = field(default_factory=list)    # reasonable defaults we will state
    ambiguity: Ambiguity = field(default_factory=Ambiguity)
    needs_tools: list[str] = field(default_factory=list)
    verify: str = "none"
    fast_path: bool = False   # produced by heuristics without an LLM call

    @property
    def primary_type(self) -> str:
        return self.task_types[0] if self.task_types else "conversational"

    def public(self) -> dict:
        """The user-facing view: what we understood, never internal deliberation."""
        return {"task_types": self.task_types, "complexity": self.complexity, "parts": self.parts,
                "assumptions": self.assumptions, "missing_info": self.missing_info,
                "interpretation": self.ambiguity.chosen if self.ambiguity.is_ambiguous else None}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Subtask:
    id: int
    goal: str
    method: str = ""          # e.g. "run_python", "web_search", "reason"
    covers: list[int] = field(default_factory=list)   # indices into TaskAnalysis.parts
    done_when: str = ""


@dataclass
class Plan:
    subtasks: list[Subtask] = field(default_factory=list)

    def steps(self) -> list[str]:
        return [s.goal for s in self.subtasks]

    def render(self) -> str:
        lines = []
        for s in self.subtasks:
            extra = []
            if s.method and s.method != "reason":
                extra.append(f"via {s.method}")
            if s.done_when:
                extra.append(f"done when: {s.done_when}")
            lines.append(f"{s.id}. {s.goal}" + (f" ({'; '.join(extra)})" if extra else ""))
        return "\n".join(lines)


@dataclass
class Verdict:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    parts_missing: list[str] = field(default_factory=list)
    note: str | None = None

    def all_issues(self) -> list[str]:
        return self.issues + [f"Not answered: {p}" for p in self.parts_missing]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.all_issues(), **({"note": self.note} if self.note else {})}
