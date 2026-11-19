"""Pure evidence rules: parsing model output, verifying quotes against page text, validating citations."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..agent.jsonutil import extract_json

QUOTE_MIN_CHARS = 20
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                            " ": " ", "…": "..."})


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE).lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def quote_in_source(quote: str, source_text: str) -> bool:
    """A quote counts only if it really occurs in the fetched page (whitespace, case and typographic
    quotes normalised). Long quotes may be elided with '...': every fragment must then appear, in order."""
    q = norm(quote).strip(" \"'")
    if len(q) < QUOTE_MIN_CHARS:
        return False
    src = norm(source_text)
    if q in src:
        return True
    parts = [p.strip(" \"'") for p in re.split(r"\s*(?:\.\.\.|\[\.\.\.\])\s*", q) if p.strip(" \"'")]
    if len(parts) < 2 or any(len(p) < 12 for p in parts):
        return False
    pos = 0
    for p in parts:
        i = src.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


@dataclass
class SubQuestion:
    question: str
    queries: list[str] = field(default_factory=list)


def parse_plan(text: str, question: str, max_sub: int, queries_per_sub: int) -> list[SubQuestion]:
    data = extract_json(text)
    items = data.get("subquestions", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out: list[SubQuestion] = []
    seen_q: set[str] = set()
    for it in items if isinstance(items, list) else []:
        if isinstance(it, str):
            it = {"question": it}
        if not isinstance(it, dict):
            continue
        sq = " ".join(str(it.get("question") or it.get("q") or "").split())[:300]
        if len(sq) < 5:
            continue
        qs = []
        for q in it.get("queries") or []:
            q = " ".join(str(q).split())[:200]
            if q and q.lower() not in seen_q:
                seen_q.add(q.lower())
                qs.append(q)
        out.append(SubQuestion(sq, qs[:queries_per_sub] or [sq[:200]]))
        if len(out) >= max_sub:
            break
    return out or [SubQuestion(question[:300], [question[:200]])]


@dataclass
class RawClaim:
    subq: int
    text: str
    quote: str
    importance: str


def parse_claims(text: str, n_subq: int, limit: int = 8) -> list[RawClaim]:
    data = extract_json(text)
    items = data.get("claims", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        claim = " ".join(str(it.get("claim", "")).split())[:500]
        quote = " ".join(str(it.get("quote", "")).split())[:800]
        if len(claim) < 10 or not quote:
            continue
        try:
            sq = int(it.get("subquestion", it.get("subq", 1))) - 1
        except (TypeError, ValueError):
            sq = 0
        imp = str(it.get("importance", "medium")).lower()
        out.append(RawClaim(min(max(sq, 0), n_subq - 1), claim, quote, imp if imp in ("high", "medium", "low") else "medium"))
        if len(out) >= limit:
            break
    return out


@dataclass
class Analysis:
    groups: list[dict]      # {"claims": [ids], "summary": str}
    conflicts: list[dict]   # {"topic": str, "positions": [{"summary": str, "claims": [ids]}], "assessment": str}


def _ids(v, valid: set[int]) -> list[int]:
    out = []
    for x in v if isinstance(v, list) else []:
        m = re.fullmatch(r"C?(\d+)", str(x).strip(), re.I)
        if m and int(m.group(1)) in valid and int(m.group(1)) not in out:
            out.append(int(m.group(1)))
    return out


def parse_analysis(text: str, valid_claims: set[int]) -> Analysis:
    data = extract_json(text)
    data = data if isinstance(data, dict) else {}
    groups = []
    for g in data.get("groups") or []:
        if isinstance(g, dict) and (ids := _ids(g.get("claims"), valid_claims)):
            groups.append({"claims": ids, "summary": str(g.get("summary", ""))[:500]})
    conflicts = []
    for c in data.get("conflicts") or []:
        if not isinstance(c, dict):
            continue
        positions = []
        for p in c.get("positions") or []:
            if isinstance(p, dict) and (ids := _ids(p.get("claims"), valid_claims)):
                positions.append({"summary": str(p.get("summary", ""))[:400], "claims": ids})
        if len(positions) >= 2:  # a conflict needs at least two evidenced positions
            conflicts.append({"topic": str(c.get("topic", ""))[:200], "positions": positions,
                              "assessment": str(c.get("assessment", ""))[:800]})
    return Analysis(groups, conflicts)


def claim_statuses(claims: list[dict], analysis: Analysis, domain_of_source: dict[int, str]) -> dict[int, dict]:
    """status per claim id: conflicting | corroborated | single_source, plus supporting source numbers.
    Corroboration requires at least two *different sites* — two pages from one domain don't count."""
    by_id = {c["n"]: c for c in claims}
    out = {c["n"]: {"status": "single_source", "supporting": [c["source"]], "conflicting": []} for c in claims}
    for g in analysis.groups:
        sources = sorted({by_id[i]["source"] for i in g["claims"]})
        domains = {domain_of_source.get(s) for s in sources}
        for i in g["claims"]:
            out[i]["supporting"] = sources
            if len(domains) >= 2:
                out[i]["status"] = "corroborated"
    for c in analysis.conflicts:
        all_ids = [i for p in c["positions"] for i in p["claims"]]
        for p in c["positions"]:
            others = sorted({by_id[i]["source"] for i in all_ids if i not in p["claims"]})
            for i in p["claims"]:
                out[i]["status"] = "conflicting"
                out[i]["conflicting"] = sorted(set(out[i]["conflicting"]) | set(others))
    return out


CITATION_RE = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")
UNSUPPORTED = "[unsupported: cited a source that was not read]"


def validate_citations(markdown: str, valid_sources: set[int]) -> tuple[str, dict]:
    """Remove citation markers that don't point at a fetched source; report what was cited and removed."""
    cited: set[int] = set()
    removed: list[str] = []

    def fix(m: re.Match) -> str:
        nums = [int(x) for x in re.findall(r"S(\d+)", m.group(1))]
        good = [n for n in nums if n in valid_sources]
        removed.extend(f"S{n}" for n in nums if n not in valid_sources)
        cited.update(good)
        # A statement whose only citations point at sources that were never read is flagged, not silently kept.
        return "[" + ", ".join(f"S{n}" for n in good) + "]" if good else UNSUPPORTED

    out = CITATION_RE.sub(fix, markdown)
    # Paragraphs/bullets with factual content but no citation (rough coverage measure, headings/tables excluded)
    blocks = [b.strip() for b in re.split(r"\n\s*\n|\n(?=[-*] )", out) if b.strip()]
    factual = [b for b in blocks if not b.startswith(("#", "|", ">")) and len(b) > 60]
    uncited = sum(1 for b in factual if not CITATION_RE.search(b))
    return out, {"cited": sorted(cited), "removed": removed,
                 "coverage": round(1 - uncited / len(factual), 2) if factual else 1.0}


def strip_model_sources_section(markdown: str) -> str:
    """The model must not write its own reference list; drop one if it did (ours is generated from data)."""
    return re.split(r"\n#{1,3}\s*(?:sources|references|bibliography|works cited)\s*\n", markdown, flags=re.I)[0].rstrip()
