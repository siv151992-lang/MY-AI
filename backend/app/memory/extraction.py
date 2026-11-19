"""Turn a conversation exchange into candidate long-term memories."""
from __future__ import annotations

from dataclasses import dataclass

from ..agent.jsonutil import extract_json
from ..llm.formats import text_msg
from .scoring import CATEGORIES, clamp, normalize_category

MAX_CANDIDATES = 5

EXTRACT_SYSTEM = """You curate long-term memory for an AI assistant. From the latest exchange, extract durable facts \
the USER stated about themselves that will help in future, unrelated conversations.

Extract: identity and role, location, languages, ongoing projects and goals, tools and stack, people they mention by \
relationship, stable preferences, and standing instructions for how the assistant should respond.
Do NOT extract: anything the assistant said, one-off questions or tasks, temporary states or moods, guesses, \
facts about other people that the user did not volunteer, passwords, keys, account or ID numbers, or sensitive \
details (health, religion, sexuality, politics, finances, legal status) unless the user explicitly asked you to \
remember them.

The user's existing memories are listed with ids. If the exchange CORRECTS or UPDATES one (e.g. they moved city, \
changed jobs, or say a memory is wrong), output the corrected fact with "supersedes" set to that id. Do not repeat \
facts that are already known.

Each memory: one short third-person sentence ("Works as a backend developer using Java").
Fields: content; category (one of: """ + ", ".join(CATEGORIES) + """); importance 1-5 (5 = shapes almost every \
answer, 1 = trivia); confidence 0-1 (how clearly the user stated it); supersedes (id or null).
Reply with JSON only: {"memories": [...]}  (an empty list when nothing durable was said)."""


@dataclass
class Candidate:
    content: str
    category: str
    importance: float  # 0..1
    confidence: float  # 0..1
    supersedes: str | None = None


def build_prompt(user_text: str, answer: str, known: list[dict]) -> str:
    known_txt = "\n".join(f'- [{m["id"]}] {m["content"]}' for m in known) or "(none)"
    return (f"<existing_memories>\n{known_txt}\n</existing_memories>\n\n"
            f"<user>\n{user_text[:4000]}\n</user>\n<assistant>\n{answer[:2000]}\n</assistant>")


def parse_candidates(text: str, known_ids: set[str] | None = None) -> list[Candidate]:
    data = extract_json(text)
    if isinstance(data, dict):
        items = data.get("memories", data.get("facts", []))
    elif isinstance(data, list):
        items = data
    else:
        items = []
    out: list[Candidate] = []
    seen: set[str] = set()
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"content": it}
        if not isinstance(it, dict):
            continue
        content = " ".join(str(it.get("content", "")).split())
        if not (3 < len(content) <= 300) or content.lower() in seen:
            continue
        seen.add(content.lower())
        try:
            imp = clamp((float(it.get("importance", 3)) - 1) / 4)
        except (TypeError, ValueError):
            imp = 0.5
        try:
            conf = clamp(float(it.get("confidence", 0.8)))
        except (TypeError, ValueError):
            conf = 0.8
        sup = it.get("supersedes")
        sup = str(sup) if sup and known_ids is not None and str(sup) in known_ids else None
        out.append(Candidate(content, normalize_category(it.get("category"), content), imp, conf, sup))
    return out[:MAX_CANDIDATES]


def norm(text: str) -> str:
    return " ".join(text.lower().strip(" .!").split())


def find_duplicate(content: str, existing: list[dict], vector_scores: bool, threshold: float = 0.92) -> dict | None:
    """Exact (normalised) match always counts; with embeddings, cosine similarity >= threshold counts too."""
    n = norm(content)
    for e in existing:
        if norm(e["content"]) == n:
            return e
        if vector_scores and e.get("sim") is not None and float(e["sim"]) >= threshold:
            return e
    return None


async def extract(llm, model: str, user_text: str, answer: str, known: list[dict]) -> list[Candidate]:
    reply = await llm.complete(model=model, system=EXTRACT_SYSTEM, max_tokens=700,
                               messages=[text_msg("user", build_prompt(user_text, answer, known))])
    return parse_candidates(reply, {str(m["id"]) for m in known})


