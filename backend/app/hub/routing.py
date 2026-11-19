"""Adaptive routing: look at a request, decide what kind of task it is, then pick the model, tools and (for big jobs)
the workspace that suits it best.

Everything here is deterministic and explainable: the decision comes with the signals that drove it, so the user can
see *why* a model was chosen. The model choice adapts to measured reliability and latency (usage analytics) and to
the user's own routing preferences."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CATEGORIES = ("chat", "coding", "reasoning", "research", "writing", "analysis", "vision")

_P = lambda *words: re.compile(r"\b(" + "|".join(words) + r")\b", re.I)  # noqa: E731

SIGNALS = {
    "coding": _P(r"code", r"function", r"bug", r"debug", r"stack ?trace", r"exception", r"compile", r"refactor",
                 r"python", r"javascript", r"typescript", r"java", r"rust", r"golang", r"sql", r"regex", r"api",
                 r"class", r"unit tests?", r"script", r"implement", r"algorithm", r"dockerfile", r"react", r"css"),
    "reasoning": _P(r"prove", r"proof", r"derive", r"calculate", r"solve", r"equation", r"probability", r"math",
                    r"logic", r"puzzle", r"step[- ]by[- ]step", r"optimi[sz]e", r"trade-?offs?", r"why does"),
    "research": _P(r"research", r"latest", r"recent", r"news", r"today", r"current", r"sources?", r"cite",
                   r"citations?", r"look up", r"search the web", r"market", r"competitors?", r"compare .* (products|tools|options)",
                   r"literature", r"studies", r"20[2-3]\d"),
    "writing": _P(r"write", r"draft", r"rewrite", r"essay", r"email", r"letter", r"blog", r"post", r"story", r"poem",
                  r"summari[sz]e", r"proofread", r"tone", r"cover letter", r"announcement", r"translate"),
    "analysis": _P(r"analy[sz]e", r"analysis", r"data", r"dataset", r"spreadsheet", r"csv", r"excel", r"table",
                   r"statistics?", r"chart", r"trend", r"report on", r"pdf", r"document", r"contract"),
}
HARD = _P(r"architecture", r"design (a|an|the)", r"in depth", r"in-depth", r"comprehensive", r"thorough",
          r"detailed", r"production", r"scalable", r"end[- ]to[- ]end", r"strategy", r"evaluate", r"critique",
          r"step[- ]by[- ]step", r"prove", r"migration", r"security review")
SIMPLE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|yes|no|good (morning|evening|night))\b[\s!.?]*$", re.I)
SCHEDULE = _P(r"every (day|morning|evening|week|monday|tuesday|wednesday|thursday|friday|hour)", r"daily", r"weekly",
              r"each (day|morning|week)", r"whenever", r"remind me", r"on a schedule", r"automatically every")
TEAM = _P(r"and then", r"multiple (parts|steps|deliverables)", r"end[- ]to[- ]end", r"full report", r"plan and execute")
BUILD = _P(r"build (me )?(a|an) (\w+ ){0,2}(app|website|service|tool|cli|api|game)", r"scaffold", r"new project", r"repository",
           r"codebase", r"multi-file")
DEEP_RESEARCH = _P(r"deep research", r"research report", r"literature review", r"market (research|analysis)",
                   r"competitive analysis", r"state of the art", r"comprehensive (overview|review|report)")


@dataclass
class Profile:
    category: str
    complexity: str                # low | medium | high
    needs: list[str]               # web | code | documents | vision
    signals: list[str]             # human-readable reasons
    scores: dict[str, int] = field(default_factory=dict)


def profile(text: str, *, has_images: bool = False, has_documents: bool = False) -> Profile:
    t = text or ""
    signals: list[str] = []
    scores = {c: len(rx.findall(t)) for c, rx in SIGNALS.items()}
    if "```" in t or re.search(r"\b(def|function|class|import|SELECT)\b.*[({:]", t):
        scores["coding"] += 3
        signals.append("contains code")
    if has_documents:
        scores["analysis"] += 2
        signals.append("documents attached")
    if has_images:
        signals.append("image attached")
    best = max(scores, key=lambda c: scores[c])
    category = best if scores[best] > 0 else "chat"
    if has_images and scores[best] <= 1:
        category = "vision"
    if scores[best] > 0:
        signals.append(f"{category} keywords ({scores[best]})")

    words = len(t.split())
    hard_hits = len(HARD.findall(t))
    parts = len(re.findall(r"(^|\n)\s*(\d+[.)]|[-*•])\s+\S", t))
    points = (2 if words > 250 else 1 if words > 60 else 0) + min(hard_hits, 3) + (1 if parts >= 3 else 0) \
        + (1 if category in ("reasoning", "coding") and words > 25 else 0)
    if SIMPLE.match(t) or (words <= 6 and category == "chat"):
        complexity = "low"
        signals.append("short, simple message")
    elif points >= 3:
        complexity = "high"
        signals.append("long or demanding request" if words > 250 else "demanding request")
    elif points >= 1 or category != "chat":
        complexity = "medium"
    else:
        complexity = "low"

    needs = []
    if category == "research" or re.search(r"\b(latest|today|current|news|this (week|month|year))\b", t, re.I):
        needs.append("web")
    if category in ("coding", "analysis") and re.search(r"\b(run|execute|test|compute|calculate|plot|chart)\b", t, re.I):
        needs.append("code")
    if has_documents or re.search(r"\b(my (documents?|files?|notes|pdfs?)|uploaded)\b", t, re.I):
        needs.append("documents")
    if has_images:
        needs.append("vision")
    return Profile(category, complexity, needs, signals, scores)


TIER_RANK = {"fast": 0, "balanced": 1, "frontier": 2}
WANTED_TIER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Choice:
    model_id: str
    score: float
    reasons: list[str]


def rank_models(prof: Profile, models: list, prefs: dict | None = None, health: dict | None = None) -> list[Choice]:
    """Score each available model for this task. Higher is better."""
    prefs, health = prefs or {}, health or {}
    priority = prefs.get("priority", "balanced")
    want = WANTED_TIER[prof.complexity] + {"speed": -1, "cost": -1, "quality": 1}.get(priority, 0)
    want = max(0, min(2, want))
    out: list[Choice] = []
    for m in models:
        reasons: list[str] = []
        if prefs.get("local_only") and m.provider != "ollama":
            continue
        if "vision" in prof.needs and not m.vision:
            continue
        if prof.needs and not m.tools and set(prof.needs) & {"web", "code", "documents"}:
            continue
        tier = TIER_RANK.get(getattr(m, "tier", "balanced"), 1)
        score = 10 - 3 * abs(tier - want)
        if tier == want:
            reasons.append(f"{getattr(m, 'tier', 'balanced')} tier fits {prof.complexity} complexity")
        strengths = set(getattr(m, "strengths", ()) or ())
        if prof.category in strengths:
            score += 2
            reasons.append(f"strong at {prof.category}")
        if prof.category == "reasoning" and m.reasoning in ("anthropic_thinking", "openai_effort"):
            score += 1
            reasons.append("native reasoning")
        if priority == "cost" and m.provider == "ollama":
            score += 2
            reasons.append("runs locally (no API cost)")
        h = health.get(m.id)
        if h and h.get("calls", 0) >= 5:
            if h["error_rate"] >= 0.5:
                score -= 8
                reasons.append(f"recent error rate {h['error_rate']:.0%}")
            elif h["error_rate"] >= 0.2:
                score -= 4
                reasons.append(f"recent error rate {h['error_rate']:.0%}")
            if priority == "speed" and h.get("p50_ms"):
                score -= min(3, h["p50_ms"] / 10_000)
        out.append(Choice(m.id, round(score, 2), reasons))
    out.sort(key=lambda c: -c.score)
    return out


@dataclass
class Route:
    model_id: str | None
    fallbacks: list[str]
    category: str
    complexity: str
    needs: list[str]
    agent_mode: bool
    reasons: list[str]
    suggestion: dict | None        # {kind: research|team|code|automation, label, reason}
    pinned: bool = False

    def public(self) -> dict:
        return asdict(self)


def suggest(text: str, prof: Profile) -> dict | None:
    """Big jobs are better done in a dedicated workspace; offer it (the chat still answers)."""
    if SCHEDULE.search(text):
        return {"kind": "automation", "label": "Create an automation",
                "reason": "You described something that should happen on a schedule or trigger."}
    if DEEP_RESEARCH.search(text) or (prof.category == "research" and prof.complexity == "high"):
        return {"kind": "research", "label": "Run Deep research",
                "reason": "A sourced, multi-step research run reads many pages and cites every claim."}
    if BUILD.search(text) and prof.category == "coding":
        return {"kind": "code", "label": "Open a code workspace",
                "reason": "A code workspace can create files, run them and keep them under version control."}
    if prof.complexity == "high" and (TEAM.search(text) or sum(1 for v in prof.scores.values() if v) >= 3):
        return {"kind": "team", "label": "Hand it to the agent team",
                "reason": "This spans several kinds of work; specialist agents can split it up and check each other."}
    return None


def route(text: str, models: list, *, prefs: dict | None = None, health: dict | None = None,
          has_images: bool = False, has_documents: bool = False) -> Route:
    prof = profile(text, has_images=has_images, has_documents=has_documents)
    prefs = prefs or {}
    ranked = rank_models(prof, models, prefs, health)
    pinned = (prefs.get("pinned") or {}).get(prof.category)
    reasons = list(prof.signals)
    is_pinned = False
    if pinned and any(c.model_id == pinned for c in ranked):
        ranked.sort(key=lambda c: c.model_id != pinned)
        reasons.append(f"you pinned {pinned} for {prof.category}")
        is_pinned = True
    if not ranked:
        return Route(None, [], prof.category, prof.complexity, prof.needs, False,
                     reasons + ["no available model meets this request's needs"], suggest(text, prof))
    reasons += ranked[0].reasons
    agent_mode = prof.complexity == "high" and bool(set(prof.needs) & {"web", "code", "documents"})
    return Route(ranked[0].model_id, [c.model_id for c in ranked[1:3]], prof.category, prof.complexity, prof.needs,
                 agent_mode, reasons, suggest(text, prof), is_pinned)


# ------------------------------------------------------------------ preferences
DEFAULT_PREFS = {"routing": {"priority": "balanced", "local_only": False, "pinned": {}}}


async def get_prefs(user_id: str) -> dict:
    from .. import db
    raw = await db.pool().fetchval("SELECT prefs FROM user_prefs WHERE user_id=$1", user_id)
    prefs = raw if isinstance(raw, dict) else ({} if raw is None else __import__("json").loads(raw))
    routing = {**DEFAULT_PREFS["routing"], **(prefs.get("routing") or {})}
    return {**prefs, "routing": routing}


async def set_routing_prefs(user_id: str, routing: dict) -> dict:
    import json

    from .. import db
    from ..llm import registry
    ids = {m.id for m in registry.available_models()}
    clean = {"priority": routing.get("priority") if routing.get("priority") in ("balanced", "speed", "quality", "cost") else "balanced",
             "local_only": bool(routing.get("local_only")),
             "pinned": {c: m for c, m in (routing.get("pinned") or {}).items() if c in CATEGORIES and m in ids}}
    cur = await get_prefs(user_id)
    cur["routing"] = clean
    await db.pool().execute(
        "INSERT INTO user_prefs (user_id, prefs) VALUES ($1, $2::jsonb) ON CONFLICT (user_id) DO UPDATE "
        "SET prefs = EXCLUDED.prefs, updated_at = now()", user_id, json.dumps(cur))
    return cur


async def route_for_user(user_id: str, text: str, *, has_images: bool = False, has_documents: bool = False) -> Route:
    from ..llm import registry
    from . import usage
    prefs = (await get_prefs(user_id))["routing"]
    return route(text, registry.available_models(), prefs=prefs, health=await usage.model_health(),
                 has_images=has_images, has_documents=has_documents)
