"""Run / step / approval records and their status vocabularies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

RUN_STATUSES = ("planning", "running", "awaiting_approval", "verifying", "completed", "completed_with_issues",
                "failed", "cancelled")
TERMINAL = {"completed", "completed_with_issues", "failed", "cancelled"}
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Step:
    idx: int
    title: str
    description: str = ""
    tool: str = "none"              # expected primary tool ("none" = pure reasoning/writing)
    success_criteria: str = ""
    status: str = "pending"
    attempts: int = 0
    result: str = ""
    error: str = ""
    id: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    origin: str = "plan"            # plan | replan | remediation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    id: str
    user_id: str
    goal: str
    status: str = "planning"
    model_id: str | None = None
    report: str = ""
    verification: dict | None = None
    error: str = ""
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Approval:
    id: str
    run_id: str
    step_idx: int
    tool: str
    args: dict
    summary: str
    status: str = "pending"          # pending | approved | denied | expired
    created_at: str = field(default_factory=now)
    decided_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionRecord:
    """Ground truth of what the agent actually did — the report is built from these, not from model claims."""
    step_idx: int
    tool: str
    args_summary: str
    ok: bool
    output: str
    approval: str | None = None      # approved | denied | None (not required)

    def to_dict(self) -> dict:
        return asdict(self)
