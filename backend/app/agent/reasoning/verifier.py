"""Verifier: checks the draft against the analysis (every part answered) and against the evidence."""
from __future__ import annotations

import json

from ...llm.formats import text_msg
from ..jsonutil import extract_json
from .types import TaskAnalysis, Verdict

VERIFIER_SYSTEM = """You are a strict, independent reviewer of an AI assistant's draft answer. You see the \
user's request, the list of parts it must cover, the tool evidence gathered, and the draft.

Check, in order:
1. Coverage: is every listed part actually answered (not just mentioned)?
2. Correctness: are calculations right (recompute key numbers yourself), is code correct, is the logic valid?
3. Grounding: are factual claims supported by the evidence or well-established knowledge? Flag invented facts, \
sources or URLs not in the evidence, and false certainty.
4. Honesty about gaps: are stated assumptions and missing information acknowledged where they matter?
{domain_checks}
Ignore style unless it hides the answer. Be specific and actionable in issues.
Return JSON only: {{"ok": true|false, "parts_missing": ["part text", ...], "issues": ["...", ...]}}"""

DOMAIN_CHECKS = {
    "math": "5. Math: if the draft states non-trivial computed numbers but the evidence contains no run_python "
            "computation of them, add the issue \"Verify the key calculations with run_python\". Check units and rounding.",
    "technical": "5. Code: if the draft presents runnable Python that was not executed in the evidence, add an issue "
                 "asking to test it with run_python (when practical). Check edge cases and error handling.",
    "logic": "5. Logic: try to find a counterexample to the conclusion; flag any step that does not follow.",
    "factual": "5. Facts: time-sensitive claims without a web source in the evidence should be hedged or verified.",
}


def should_verify(analysis: TaskAnalysis, used_tools: bool, answer: str) -> bool:
    if len(answer.strip()) < 40:
        return False
    if analysis.verify == "strict":
        return True
    if analysis.verify == "light":
        return used_tools or len(analysis.parts) > 1 or len(answer) > 600
    return used_tools and len(answer) > 300


def parse_verdict(text: str, parts: list[str]) -> Verdict:
    data = extract_json(text)
    if not isinstance(data, dict) or "ok" not in data:
        return Verdict(ok=True, note="verifier reply unparseable; accepted draft")
    issues = [str(i).strip() for i in data.get("issues") or [] if str(i).strip()]
    missing = [str(p).strip() for p in data.get("parts_missing") or [] if str(p).strip()]
    ok = (data["ok"] is True or str(data["ok"]).lower() == "true") and not missing
    return Verdict(ok=ok, issues=[] if ok else issues, parts_missing=[] if ok else missing)


class LLMVerifier:
    def __init__(self, llm, model: str):
        self.llm, self.model = llm, model

    async def verify(self, analysis: TaskAnalysis, user_text: str, answer: str, evidence: list[str]) -> Verdict:
        checks = "\n".join(DOMAIN_CHECKS[t] for t in dict.fromkeys(analysis.task_types) if t in DOMAIN_CHECKS)
        system = VERIFIER_SYSTEM.format(domain_checks=checks)
        ev = "\n\n".join(e[:3000] for e in evidence[-10:]) or "(no tool evidence)"
        prompt = (f"<request>\n{user_text[:6000]}\n</request>\n\n<parts>\n{json.dumps(analysis.parts, ensure_ascii=False)}\n</parts>\n\n"
                  f"<assumptions>\n{json.dumps(analysis.assumptions, ensure_ascii=False)}\n</assumptions>\n\n"
                  f"<tool_evidence>\n{ev}\n</tool_evidence>\n\n<draft_answer>\n{answer}\n</draft_answer>")
        reply = await self.llm.complete(model=self.model, system=system, max_tokens=800,
                                        messages=[text_msg("user", prompt)])
        return parse_verdict(reply, analysis.parts)
