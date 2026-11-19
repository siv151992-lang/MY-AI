"""Goal verification and the final execution report.

The report's factual sections (steps, actions, approvals, errors, files) are generated from recorded data by
code. The model only writes the short narrative summary, and it is given nothing but those same facts.
"""
from __future__ import annotations

import json
import re

from ..agent.jsonutil import extract_json
from ..llm.formats import text_msg
from .models import ActionRecord, Approval, Run, Step

VERIFY_SYSTEM = """You verify whether an autonomous agent actually achieved the user's goal. You are given the goal, \
the executed steps with results, and the log of real tool actions (the only ground truth — step summaries are \
claims). Judge whether the goal is achieved, partially achieved, or not achieved. A claimed action without a \
matching successful tool action does NOT count.
Return JSON only: {"achieved": "yes"|"partial"|"no", "issues": ["specific gap", ...], "evidence": "one sentence"}"""

SUMMARY_SYSTEM = """Write a short executive summary (3-6 sentences, markdown allowed) of an autonomous agent run for \
the user, using ONLY the facts provided. State what was achieved, what was not, and anything the user must do next. \
Do not invent actions, results, links or numbers that are not in the facts."""

STATUS_ICON = {"done": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏸️", "running": "🔄"}


def actions_text(actions: list[ActionRecord]) -> str:
    return "\n".join(f"[step {a.step_idx + 1}] {a.tool}: {a.args_summary[:200]} -> "
                     f"{'OK' if a.ok else 'FAILED'}{' (approval: ' + a.approval + ')' if a.approval else ''}: "
                     f"{a.output[:400]}" for a in actions) or "(no tool actions)"


def steps_text(steps: list[Step]) -> str:
    return "\n".join(f"{s.idx + 1}. [{s.status}] {s.title} — {s.result[:500] or s.error[:300]}" for s in steps)


def parse_goal_verdict(text: str) -> dict:
    data = extract_json(text)
    if not isinstance(data, dict) or data.get("achieved") not in ("yes", "partial", "no"):
        return {"achieved": "partial", "issues": ["Verifier response could not be parsed"], "evidence": ""}
    return {"achieved": data["achieved"], "issues": [str(i) for i in data.get("issues") or [] if str(i).strip()],
            "evidence": str(data.get("evidence") or "")}


async def verify_goal(llm, model: str, goal: str, steps: list[Step], actions: list[ActionRecord]) -> dict:
    prompt = (f"<goal>\n{goal}\n</goal>\n\n<steps>\n{steps_text(steps)}\n</steps>\n\n"
              f"<tool_actions>\n{actions_text(actions)}\n</tool_actions>")
    reply = await llm.complete(model=model, system=VERIFY_SYSTEM, max_tokens=700, messages=[text_msg("user", prompt)])
    return parse_goal_verdict(reply)


def _cell(s: str, n: int = 160) -> str:
    return re.sub(r"\s+", " ", s or "").replace("|", "\\|")[:n]


def build_report(run: Run, steps: list[Step], actions: list[ActionRecord], approvals: list[Approval],
                 verification: dict | None, summary: str, errors: list[dict]) -> str:
    outcome = {"completed": "✅ Goal achieved", "completed_with_issues": "⚠️ Partially achieved",
               "failed": "❌ Not achieved", "cancelled": "⏹️ Cancelled"}.get(run.status, run.status)
    out = [f"# Execution report", "", f"**Goal:** {run.goal}", "", f"**Outcome:** {outcome}", ""]
    if summary:
        out += ["## Summary", "", summary.strip(), ""]

    out += ["## Steps", "", "| # | Step | Status | Attempts | Result |", "|---|---|---|---|---|"]
    for s in steps:
        out.append(f"| {s.idx + 1} | {_cell(s.title, 80)}{' *(re-planned)*' if s.origin != 'plan' else ''} | "
                   f"{STATUS_ICON.get(s.status, '')} {s.status} | {s.attempts} | {_cell(s.result or s.error)} |")
    out.append("")

    out += ["## Actions performed", ""]
    if actions:
        out += ["| Step | Tool | Action | Result |", "|---|---|---|---|"]
        for a in actions:
            res = ("✅ " if a.ok else "❌ ") + _cell(a.output, 120)
            appr = f" *(you {a.approval})*" if a.approval else ""
            out.append(f"| {a.step_idx + 1} | `{a.tool}` | {_cell(a.args_summary, 100)}{appr} | {res} |")
    else:
        out.append("_No tool actions were performed._")
    out.append("")

    if approvals:
        out += ["## Permission requests", ""]
        out += [f"- **{a.status}** — `{a.tool}`: {_cell(a.summary, 200)}" for a in approvals]
        out.append("")

    if errors:
        out += ["## Errors and recovery", ""]
        for e in errors:
            out.append(f"- Step {e['step'] + 1}, attempt {e['attempt']}: {_cell(e['error'], 300)}"
                       + (f" → {e['recovery']}" if e.get("recovery") else ""))
        out.append("")

    files = []
    for a in actions:
        if a.tool == "create_file" and a.ok:
            m = re.search(r"Created file (.+?) \(.*?download=(\S+)", a.output)
            if m:
                files.append(f"- [{m.group(1)}]({m.group(2)})")
    if files:
        out += ["## Files created", "", *files, ""]

    if verification:
        out += ["## Verification", "",
                f"**Goal achieved:** {verification.get('achieved', '?')}"
                + (f" — {verification['evidence']}" if verification.get("evidence") else "")]
        out += [f"- {i}" for i in verification.get("issues", [])]
        out.append("")
    if run.error:
        out += ["## Run error", "", run.error, ""]
    return "\n".join(out).strip() + "\n"


async def write_summary(llm, model: str, run: Run, steps: list[Step], actions: list[ActionRecord],
                        verification: dict | None) -> str:
    facts = {"goal": run.goal, "final_status": run.status, "steps": steps_text(steps),
             "tool_actions": actions_text(actions), "verification": verification, "run_error": run.error}
    return await llm.complete(model=model, system=SUMMARY_SYSTEM, max_tokens=500,
                              messages=[text_msg("user", json.dumps(facts, ensure_ascii=False))])
