"""Memory engine: long-term user memories + long-conversation context management."""
from __future__ import annotations

import logging

from ..llm.formats import text_msg
from .jsonutil import extract_json

log = logging.getLogger("aria.memory")

EXTRACT_SYSTEM = """You maintain long-term memory for an AI assistant. From the latest exchange, extract durable \
facts the USER stated about themselves that would help in future conversations: name, role, projects, tools/stack, \
stable preferences about how they want answers. Do NOT extract: things the assistant said, one-off questions, \
temporary moods, anything the user didn't state, secrets, passwords, ID or financial account numbers, or sensitive \
health/identity details unless the user explicitly asked to remember them.
Each fact: one short third-person sentence ("Works as a backend developer using Java").
Reply with JSON only: {"facts": [...]}  (empty list if nothing durable)."""

SUMMARY_SYSTEM = """Summarise this earlier part of a conversation so the assistant can continue it seamlessly. \
Keep: the user's goals, decisions made, key facts, numbers, code/file names, open questions. Be concise (under 300 words)."""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def message_tokens(m: dict) -> int:
    total = 0
    for p in m["content"]:
        if p["type"] == "text":
            total += estimate_tokens(p["text"])
        elif p["type"] == "image":
            total += 1500
        else:
            total += estimate_tokens(str(p.get("content") or p.get("input") or ""))
    return total


def split_history(messages: list[dict], budget: int, keep_min: int = 4) -> int:
    """Return index where the 'recent' window starts so recent messages fit in `budget` tokens.
    Messages before the index should be represented by a summary."""
    used = 0
    idx = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        cost = message_tokens(messages[i])
        if used + cost > budget and len(messages) - i > keep_min:
            break
        used += cost
        idx = i
    # never start the window on an assistant turn
    while idx < len(messages) and messages[idx]["role"] != "user":
        idx += 1
    return idx


def parse_facts(text: str) -> list[str]:
    data = extract_json(text)
    facts = data.get("facts", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    clean = []
    for f in facts:
        f = str(f).strip()
        if 3 < len(f) <= 300:
            clean.append(f)
    return clean[:5]


def is_duplicate(fact: str, existing: list[dict], vector_scores: bool, threshold: float = 0.92) -> bool:
    """Exact (normalised) match always counts; with vector search, cosine similarity >= threshold counts too."""
    norm = fact.lower().strip(" .")
    for e in existing:
        if e["content"].lower().strip(" .") == norm:
            return True
        if vector_scores and float(e.get("score") or 0) >= threshold:
            return True
    return False


async def retrieve_memories(user_id: str, query: str, k: int = 8) -> list[str]:
    from .. import db
    from ..llm import registry
    try:
        emb = await registry.embed([query]) if query else None
        hits = await db.search_memories(user_id, query, emb[0] if emb else None, k)
        if not hits and not emb:
            hits = (await db.list_memories(user_id))[:k]
        return [h["content"] for h in hits]
    except Exception:  # memory must never break chat
        log.exception("memory retrieval failed")
        return []


async def extract_and_store(user_id: str, conv_id: str, user_text: str, answer: str) -> list[str]:
    from .. import db
    from ..llm import registry
    try:
        spec = registry.utility_model()
        llm = registry.provider_for(spec)
        reply = await llm.complete(model=spec.model, system=EXTRACT_SYSTEM, max_tokens=400, messages=[
            text_msg("user", f"<user>\n{user_text[:4000]}\n</user>\n<assistant>\n{answer[:2000]}\n</assistant>")])
        saved = []
        for fact in parse_facts(reply):
            emb = await registry.embed([fact])
            existing = await db.search_memories(user_id, fact, emb[0] if emb else None, k=3)
            if is_duplicate(fact, existing, vector_scores=emb is not None):
                continue
            await db.add_memory(user_id, fact, emb[0] if emb else None, conv_id)
            saved.append(fact)
        return saved
    except Exception:
        log.exception("memory extraction failed")
        return []


async def summarize(llm, model: str, messages: list[dict], previous_summary: str | None) -> str:
    lines = []
    if previous_summary:
        lines.append(f"(Earlier summary)\n{previous_summary}")
    for m in messages:
        txt = " ".join(p.get("text", "") for p in m["content"] if p["type"] == "text")
        lines.append(f"{m['role'].upper()}: {txt[:3000]}")
    return await llm.complete(model=model, system=SUMMARY_SYSTEM, max_tokens=600,
                              messages=[text_msg("user", "\n\n".join(lines))])
