"""Conversation state: structured working memory that keeps long conversations coherent.

Complements the rolling summary: instead of prose, it tracks the user's goals, constraints, decisions,
key facts/numbers, artifacts and open questions, updated after every turn and injected into each prompt.
"""
from __future__ import annotations

import json

from ...llm.formats import text_msg
from ..jsonutil import extract_json

FIELDS: dict[str, str] = {
    "goals": "what the user is trying to achieve in this conversation",
    "constraints": "requirements and limits the user set (stack, budget, format, deadlines)",
    "decisions": "choices already made, so they are not re-litigated",
    "key_facts": "important facts, numbers and results established so far",
    "artifacts": "files, code modules, documents or names being worked on",
    "open_questions": "unresolved questions or pending next steps",
}
MAX_ITEMS = 8
MAX_ITEM_CHARS = 240

STATE_SYSTEM = """You maintain the working state of a conversation between a user and an AI assistant. \
Update the JSON state using the latest exchange. Keep items short and specific. Merge duplicates, drop items that \
are resolved or superseded (e.g. an answered open question, a changed decision), and keep at most 8 items per field. \
Only record what the user said or what was actually established — never speculation.
Fields: """ + "; ".join(f"{k}: {v}" for k, v in FIELDS.items()) + """.
Return the complete updated state as JSON only, with exactly these keys."""


def empty_state() -> dict:
    return {k: [] for k in FIELDS}


def normalize_state(data) -> dict:
    out = empty_state()
    if not isinstance(data, dict):
        return out
    for k in FIELDS:
        v = data.get(k)
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            seen = set()
            for item in v:
                s = str(item).strip()[:MAX_ITEM_CHARS]
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    out[k].append(s)
            out[k] = out[k][-MAX_ITEMS:]
    return out


def render_state(state: dict | None) -> str:
    state = normalize_state(state or {})
    lines = []
    for k in FIELDS:
        if state[k]:
            lines.append(f"{k.replace('_', ' ').title()}:")
            lines.extend(f"- {x}" for x in state[k])
    return "\n".join(lines)


async def update_state(llm, model: str, state: dict | None, user_text: str, answer: str) -> dict:
    prompt = (f"<current_state>\n{json.dumps(normalize_state(state or {}), ensure_ascii=False)}\n</current_state>\n\n"
              f"<latest_exchange>\nUSER: {user_text[:4000]}\n\nASSISTANT: {answer[:4000]}\n</latest_exchange>")
    reply = await llm.complete(model=model, system=STATE_SYSTEM, max_tokens=900, messages=[text_msg("user", prompt)])
    data = extract_json(reply)
    return normalize_state(data) if isinstance(data, dict) else normalize_state(state or {})
