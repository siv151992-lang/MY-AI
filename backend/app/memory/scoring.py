"""Importance scoring and retrieval ranking.

importance            stored 0..1, set at creation from category, source and the extractor's hint, raised
                      when the same fact is seen again, and freely editable by the user.
effective importance  importance decayed by time since last use (half-life), plus a small reinforcement
                      from how often the memory was actually used. Pinned memories never decay.
retrieval score       relevance (cosine similarity fused with keyword rank via reciprocal-rank fusion)
                      blended with effective importance and recency.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

CATEGORIES: dict[str, float] = {
    "instruction": 0.80,   # how the user wants the assistant to behave ("always answer in British English")
    "profile": 0.70,       # who they are: name, role, location, languages
    "preference": 0.65,    # likes, dislikes, tools, styles
    "project": 0.60,       # ongoing work, goals
    "relationship": 0.55,  # people in their life or work
    "fact": 0.50,          # other durable facts
    "other": 0.40,
}
SOURCE_BOOST = {"manual": 0.15, "explicit": 0.15, "import": 0.05, "extracted": 0.0}

DECAY_HALF_LIFE_DAYS = 120.0
RECENCY_HALF_LIFE_DAYS = 30.0
RRF_K = 60
WEIGHTS = {"relevance": 0.65, "importance": 0.25, "recency": 0.10}
MIN_SIMILARITY = 0.25  # vector-only hits below this are noise


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


_CATEGORY_HINTS: list[tuple[str, re.Pattern]] = [
    ("instruction", re.compile(r"\b(always|never|please|don'?t|do not|make sure|answer|respond|reply|format|call me)\b", re.I)),
    ("relationship", re.compile(r"\b(wife|husband|partner|son|daughter|mother|father|mom|dad|brother|sister|friend|"
                                r"colleague|manager|boss|team ?mate|kids?|children)\b", re.I)),
    ("profile", re.compile(r"\b(name is|is called|works? (as|at)|lives? in|based in|born|years old|speaks?|"
                           r"is an? |role|job|studies|student)\b", re.I)),
    ("project", re.compile(r"\b(project|building|working on|startup|launch|deadline|thesis|app|repo|product)\b", re.I)),
    ("preference", re.compile(r"\b(prefers?|likes?|loves?|enjoys?|hates?|dislikes?|favou?rite|wants?|uses?)\b", re.I)),
]


def guess_category(text: str) -> str:
    for cat, pat in _CATEGORY_HINTS:
        if pat.search(text):
            return cat
    return "fact"


def normalize_category(cat: str | None, text: str = "") -> str:
    c = (cat or "").strip().lower()
    return c if c in CATEGORIES else guess_category(text)


def initial_importance(category: str, source: str, hint: float | None = None, confidence: float = 1.0) -> float:
    """hint: the extractor's 0..1 importance estimate (None for manual entries)."""
    base = CATEGORIES.get(category, CATEGORIES["other"])
    score = base if hint is None else 0.5 * base + 0.5 * clamp(hint)
    score += SOURCE_BOOST.get(source, 0.0)
    score *= 0.7 + 0.3 * clamp(confidence)  # uncertain facts matter less
    return round(clamp(score, 0.05, 1.0), 3)


def reinforce(importance: float, amount: float = 0.05) -> float:
    return round(clamp(importance + amount * (1 - importance) * 2, 0.05, 1.0), 3)


def _days(ts: datetime | None, now: datetime) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400)


def effective_importance(m: dict, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    imp = float(m.get("importance") or 0.5)
    if m.get("pinned"):
        return 1.0
    idle = _days(m.get("last_accessed_at") or m.get("updated_at") or m.get("created_at"), now)
    decay = 0.5 ** (idle / DECAY_HALF_LIFE_DAYS)
    boost = min(0.15, 0.04 * math.log1p(int(m.get("access_count") or 0)))
    # decay toward a floor so old-but-important facts are never forgotten by scoring alone
    return round(clamp(imp * (0.4 + 0.6 * decay) + boost), 4)


def recency(m: dict, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    ts = m.get("last_accessed_at") or m.get("updated_at") or m.get("created_at")
    return 0.5 ** (_days(ts, now) / RECENCY_HALF_LIFE_DAYS)


def rrf(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal-rank fusion, normalised so an item ranked first in every list scores 1.0."""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    top = len([lst for lst in ranked_lists if lst]) / (k + 1) or 1.0
    return {key: s / top for key, s in scores.items()}


def fuse_and_rank(vector_hits: list[dict], keyword_hits: list[dict], now: datetime | None = None,
                  weights: dict | None = None, min_similarity: float = MIN_SIMILARITY) -> list[dict]:
    """Merge vector (with `sim`) and keyword hits for the same items, and rank them.

    Each returned dict gains: relevance, effective_importance, score. Vector-only hits below
    `min_similarity` are dropped; keyword matches are kept regardless.
    """
    w = weights or WEIGHTS
    now = now or datetime.now(timezone.utc)
    by_id: dict[str, dict] = {}
    for h in keyword_hits:
        by_id[str(h["id"])] = {**h, "keyword": True}
    for h in vector_hits:
        sim = float(h.get("sim") or 0.0)
        key = str(h["id"])
        if key in by_id:
            by_id[key]["sim"] = sim
        elif sim >= min_similarity:
            by_id[key] = {**h, "keyword": False}
    fused = rrf([[str(h["id"]) for h in vector_hits if str(h["id"]) in by_id],
                 [str(h["id"]) for h in keyword_hits]])
    out = []
    for key, m in by_id.items():
        sim = m.get("sim")
        rel = 0.7 * clamp(float(sim)) + 0.3 * fused.get(key, 0.0) if sim is not None else fused.get(key, 0.0)
        eff = effective_importance(m, now)
        m["relevance"] = round(rel, 4)
        m["effective_importance"] = eff
        m["score"] = round(w["relevance"] * rel + w["importance"] * eff + w["recency"] * recency(m, now), 4)
        out.append(m)
    out.sort(key=lambda m: m["score"], reverse=True)
    return out
