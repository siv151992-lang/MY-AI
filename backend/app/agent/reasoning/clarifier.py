"""Ambiguity & missing-information policy: decide whether to ask, or proceed with stated assumptions."""
from __future__ import annotations

from dataclasses import dataclass

from .types import TaskAnalysis


@dataclass
class ClarifyDecision:
    ask: bool
    message: str = ""        # what to send the user when asking
    proceed_note: str = ""   # guidance for the answering model when proceeding


def decide(analysis: TaskAnalysis, *, agent_mode: bool = False, already_asked: bool = False) -> ClarifyDecision:
    """Ask only when it is genuinely needed and we haven't just asked (never loop on questions).

    - Missing information that blocks the task (e.g. "fix the bug in my file" with no file) -> ask.
    - Ambiguity where the analyzer judged a wrong guess costly -> ask.
    - Otherwise proceed, stating the chosen interpretation and assumptions in the answer.
    """
    amb = analysis.ambiguity
    blocking = analysis.missing_info and analysis.complexity != "simple"
    if not already_asked and (blocking or amb.ask):
        lines: list[str] = []
        if amb.ask and amb.question:
            lines.append(amb.question)
            if amb.interpretations:
                lines.append("")
                lines.extend(f"- {i}" for i in amb.interpretations)
        if blocking:
            if lines:
                lines.append("")
            lines.append("To do this well I also need:" if lines else "To do this well I need:")
            lines.extend(f"- {m}" for m in analysis.missing_info)
        if analysis.assumptions:
            lines.append("")
            lines.append("If you'd rather I just go ahead, I'll assume: " + "; ".join(analysis.assumptions) + ".")
        return ClarifyDecision(ask=True, message="\n".join(lines).strip())

    notes = []
    if amb.is_ambiguous and amb.chosen:
        notes.append(f"The request is ambiguous; proceed with this interpretation and say so in one short line: {amb.chosen}")
    if analysis.assumptions:
        notes.append("Adopt and briefly state these assumptions: " + "; ".join(analysis.assumptions))
    if analysis.missing_info:
        notes.append("Some details are missing (" + "; ".join(analysis.missing_info) + "). Give the best answer "
                     "possible, make clear what depends on them, and invite the user to supply them.")
    return ClarifyDecision(ask=False, proceed_note="\n".join(notes))
