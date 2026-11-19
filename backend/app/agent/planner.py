"""Planning engine: decides whether a task needs decomposition and produces a step plan."""
from __future__ import annotations

import re

from ..llm.formats import text_msg
from .jsonutil import extract_json

_COMPLEX_HINTS = re.compile(
    r"\b(research|compare|analy[sz]e|investigate|step[- ]by[- ]step|plan|design|build|implement|debug|"
    r"write (a|an) (report|essay|program|script)|pros and cons|evaluate|summari[sz]e .* (and|then))\b", re.I)

PLANNER_SYSTEM = """You are the planning module of an AI assistant. Break the user's task into 2-6 concrete, \
ordered steps the assistant will carry out (using tools such as web_search, fetch_url, run_python, \
search_documents when useful). The final step should always be producing the answer for the user.
Reply with JSON only: {"steps": ["...", "..."]}"""


def needs_plan(user_text: str, agent_mode: bool, has_documents: bool) -> bool:
    if agent_mode:
        return True
    words = len(user_text.split())
    if words > 120:
        return True
    if _COMPLEX_HINTS.search(user_text) and (words >= 8 or has_documents):
        return True
    return False


def parse_plan(text: str) -> list[str]:
    data = extract_json(text)
    steps = data.get("steps") if isinstance(data, dict) else data if isinstance(data, list) else None
    if not steps:
        # Fallback: numbered/bulleted lines
        steps = [re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", ln).strip() for ln in text.splitlines()
                 if re.match(r"^\s*(\d+[.)]|[-*])\s+", ln)]
    return [str(s).strip() for s in steps if str(s).strip()][:6]


async def make_plan(llm, model: str, user_text: str, context_hint: str = "") -> list[str]:
    prompt = user_text if not context_hint else f"{context_hint}\n\nTask: {user_text}"
    reply = await llm.complete(model=model, system=PLANNER_SYSTEM, messages=[text_msg("user", prompt)], max_tokens=600)
    return parse_plan(reply)
