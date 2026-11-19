"""Task analyzer: understands the request before anything is answered.

Decomposes multi-part questions, classifies the reasoning domain, spots missing information and
ambiguity, and decides how much effort and verification the task deserves. Trivial messages take a
heuristic fast path (no LLM call) so small talk stays instant.
"""
from __future__ import annotations

import re

from ...llm.formats import text_msg
from ..jsonutil import extract_json
from .types import TASK_TYPES, Ambiguity, TaskAnalysis

ANALYZER_SYSTEM = """You are the analysis module of an AI assistant. You do NOT answer the user. You study their \
latest message (using the conversation context to resolve references like "it", "the same", "that file") and \
describe the task precisely.

Return JSON only, with exactly these keys:
{
  "task_types": [one or more of "math","logic","analytical","technical","factual","creative","conversational"; most important first],
  "complexity": "simple" | "moderate" | "complex",
  "goal": "one sentence: what the user ultimately wants",
  "parts": ["each distinct question or deliverable the answer must cover, in the user's order"],
  "missing_info": ["information that is genuinely required, absent from context, and cannot be sensibly assumed"],
  "assumptions": ["reasonable defaults the assistant should adopt and state, instead of asking"],
  "ambiguity": {
    "is_ambiguous": true|false,
    "interpretations": ["materially different readings, only if is_ambiguous"],
    "chosen": "the most likely reading, or null",
    "ask": true|false,
    "question": "one short clarifying question if ask is true, else null"
  },
  "needs_tools": [any of "web_search","fetch_url","run_python","search_documents"],
  "verify": "none" | "light" | "strict"
}

Guidance:
- Split compound requests into separate parts ("compare X and Y and recommend one" = 3 parts).
- Set ask=true ONLY when a wrong guess would waste substantial effort or produce a wrong result AND the readings \
lead to genuinely different answers. Otherwise pick the likeliest reading, put it in "chosen", and list it as an assumption.
- missing_info is for things like an unspecified file the user refers to but never attached, or a required \
number that is absent. Do not list things you can look up with tools.
- needs_tools: run_python for any non-trivial arithmetic, statistics, unit conversion or code testing; web_search \
for current or niche facts; search_documents when the user refers to their uploaded files.
- verify: "strict" for calculations, code, factual claims that matter, or multi-step logic; "light" for moderate \
analysis; "none" for chit-chat and simple creative writing."""

_SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|thx|ok(ay)?|cool|great|good (morning|afternoon|evening|night)|"
                         r"bye|yes|no|sure|nice|got it)[\s!.,?]*$", re.I)
_MATH_HINT = re.compile(r"(\d\s*[-+*/^%]\s*\d|\d\s*%|\bsolve\b|\bequation|\bintegral|\bderivative|\bprobability|\bpercent|"
                        r"\bcompound interest|\bhow many\b|\bcalculate|\bcompute)", re.I)
_MULTI_PART = re.compile(r"(\?.*\?|\b(and also|additionally|then|as well as|plus)\b|^\s*\d+[.)]\s)", re.I | re.M)


def fast_path(user_text: str, has_files: bool) -> TaskAnalysis | None:
    """Skip the LLM analysis for messages that are obviously simple."""
    t = user_text.strip()
    if has_files:
        return None
    if _SMALL_TALK.match(t):
        return TaskAnalysis(task_types=["conversational"], complexity="simple", goal="small talk", fast_path=True)
    # Short single factual questions ("What is the capital of Peru?"). Instructions, however short,
    # always get a full analysis — "convert the report" can hide ambiguity or missing information.
    words = len(t.split())
    if t.endswith("?") and words <= 12 and not _MATH_HINT.search(t) and not _MULTI_PART.search(t):
        return TaskAnalysis(task_types=["factual"], complexity="simple", goal=t, parts=[t], verify="none",
                            fast_path=True)
    return None


def _str_list(v, limit: int = 8) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:limit]


def parse_analysis(text: str, user_text: str) -> TaskAnalysis:
    """Tolerant parser: any missing or malformed field falls back to a safe default."""
    data = extract_json(text)
    if not isinstance(data, dict):
        return TaskAnalysis(task_types=["analytical"], complexity="moderate", goal=user_text[:200],
                            parts=[user_text[:300]], verify="light")
    types = [t for t in _str_list(data.get("task_types")) if t in TASK_TYPES] or ["analytical"]
    complexity = data.get("complexity") if data.get("complexity") in ("simple", "moderate", "complex") else "moderate"
    amb = data.get("ambiguity") if isinstance(data.get("ambiguity"), dict) else {}
    interpretations = _str_list(amb.get("interpretations"), 4)
    is_amb = bool(amb.get("is_ambiguous")) and len(interpretations) >= 2
    question = str(amb.get("question") or "").strip() or None
    ambiguity = Ambiguity(is_ambiguous=is_amb, interpretations=interpretations if is_amb else [],
                          chosen=(str(amb.get("chosen")).strip() or None) if is_amb and amb.get("chosen") else None,
                          ask=is_amb and bool(amb.get("ask")) and question is not None,
                          question=question if is_amb else None)
    verify = data.get("verify") if data.get("verify") in ("none", "light", "strict") else "light"
    tools = [t for t in _str_list(data.get("needs_tools")) if t in ("web_search", "fetch_url", "run_python", "search_documents")]
    parts = _str_list(data.get("parts"), 10) or [user_text[:300]]
    return TaskAnalysis(task_types=types, complexity=complexity, goal=str(data.get("goal") or user_text[:200]),
                        parts=parts, missing_info=_str_list(data.get("missing_info"), 5),
                        assumptions=_str_list(data.get("assumptions"), 6), ambiguity=ambiguity,
                        needs_tools=tools, verify=verify)


class LLMAnalyzer:
    """Default analyzer. Any object with the same `analyze` signature can replace it."""

    def __init__(self, llm, model: str, context_turns: int = 6):
        self.llm, self.model, self.context_turns = llm, model, context_turns

    async def analyze(self, user_text: str, *, history: list[dict], state_text: str = "", has_files: bool = False,
                      tool_names: list[str] | None = None) -> TaskAnalysis:
        quick = fast_path(user_text, has_files)
        if quick:
            return quick
        ctx_lines = []
        for m in history[-self.context_turns:]:
            txt = " ".join(p.get("text", "") for p in m["content"] if p["type"] == "text")
            ctx_lines.append(f"{m['role'].upper()}: {txt[:1200]}")
        prompt = ""
        if state_text:
            prompt += f"<conversation_state>\n{state_text}\n</conversation_state>\n\n"
        if ctx_lines:
            prompt += "<recent_conversation>\n" + "\n".join(ctx_lines) + "\n</recent_conversation>\n\n"
        if has_files:
            prompt += "(The user attached files to this message.)\n"
        if tool_names:
            prompt += f"(Tools available: {', '.join(tool_names)})\n"
        prompt += f"<latest_user_message>\n{user_text[:8000]}\n</latest_user_message>"
        reply = await self.llm.complete(model=self.model, system=ANALYZER_SYSTEM,
                                        messages=[text_msg("user", prompt)], max_tokens=900)
        a = parse_analysis(reply, user_text)
        if tool_names:
            a.needs_tools = [t for t in a.needs_tools if t in tool_names]
        return a
