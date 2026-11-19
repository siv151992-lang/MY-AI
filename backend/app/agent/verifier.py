"""Verification engine: an independent check of the draft answer before it is finalised."""
from __future__ import annotations

from ..llm.formats import text_msg
from .jsonutil import extract_json

VERIFIER_SYSTEM = """You are a strict reviewer checking an AI assistant's draft answer before it reaches the user.
Check: (1) does it fully answer what was asked, including every part of a multi-part request; \
(2) are factual claims supported by the tool evidence provided (or well-established knowledge) — flag anything \
invented, contradicted by the evidence, or stated with false certainty; (3) are calculations and code correct; \
(4) are cited sources real ones that appear in the evidence.
Be pragmatic: minor style issues are not failures.
Reply with JSON only: {"ok": true|false, "issues": ["specific, actionable problem", ...]}"""


def should_verify(used_tools: bool, had_plan: bool, answer: str) -> bool:
    return (used_tools or had_plan) and len(answer) > 200


def parse_verdict(text: str) -> dict:
    data = extract_json(text)
    if not isinstance(data, dict) or "ok" not in data:
        return {"ok": True, "issues": [], "note": "verifier reply unparseable; accepted draft"}
    issues = [str(i) for i in data.get("issues") or [] if str(i).strip()]
    ok = data["ok"] is True or str(data["ok"]).lower() == "true"
    return {"ok": ok, "issues": issues if not ok else []}


async def verify(llm, model: str, question: str, answer: str, evidence: list[str]) -> dict:
    ev = "\n\n".join(e[:3000] for e in evidence[-8:]) or "(no tool evidence; judge on general knowledge)"
    prompt = (f"<question>\n{question}\n</question>\n\n<tool_evidence>\n{ev}\n</tool_evidence>\n\n"
              f"<draft_answer>\n{answer}\n</draft_answer>")
    reply = await llm.complete(model=model, system=VERIFIER_SYSTEM, messages=[text_msg("user", prompt)], max_tokens=600)
    return parse_verdict(reply)
