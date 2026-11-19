"""Retrieval-augmented generation over the user's documents.

Retrieval is always restricted to documents the user may read (their own plus ones shared with them);
the access check happens here, in SQL, not in the prompt."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..db import or_query, pool, rows, vec
from ..memory.scoring import rrf
from . import access
from .chunking import location


@dataclass
class Passage:
    n: int
    file_id: str
    filename: str
    doc_type: str
    page: int | None
    section: str
    content: str
    score: float

    @property
    def where(self) -> str:
        return location(self.page, self.section, self.doc_type)

    def cite(self) -> dict:
        return {"n": self.n, "file_id": self.file_id, "filename": self.filename, "page": self.page,
                "section": self.section, "location": self.where, "snippet": self.content[:400]}


async def _embed(texts: list[str]):
    from ..llm import registry
    try:
        return await registry.embed(texts)
    except Exception:  # noqa: BLE001 - fall back to keyword search
        return None


async def retrieve(user_id: str, query: str, file_ids: list[str] | None = None, k: int = 8,
                   per_doc: int = 0) -> list[Passage]:
    """Top passages for `query`. With per_doc > 0, every listed document contributes at least that many
    passages (used for multi-document comparison)."""
    allowed = await access.filter_readable(user_id, file_ids) if file_ids else await access.accessible_ids(user_id)
    if not allowed or not query.strip():
        return []
    emb = await _embed([query[:2000]])
    base = ("SELECT c.id, c.file_id, f.filename, COALESCE(f.doc_type, 'txt') AS doc_type, c.page, c.section, c.content{extra} "
            "FROM document_chunks c JOIN files f ON f.id=c.file_id WHERE c.file_id = ANY($1::uuid[]) ")
    vh: list[dict] = []
    if emb:
        vh = rows(await pool().fetch(base.format(extra=", 1 - (c.embedding <=> $2::vector) AS sim")
                                     + f"AND c.embedding IS NOT NULL ORDER BY c.embedding <=> $2::vector LIMIT {k * 4}",
                                     allowed, vec(emb[0])))
    kh: list[dict] = []
    if q := or_query(query):
        kh = rows(await pool().fetch(base.format(extra=", ts_rank(c.tsv, websearch_to_tsquery('english', $2)) AS kw")
                                     + f"AND c.tsv @@ websearch_to_tsquery('english', $2) ORDER BY kw DESC LIMIT {k * 4}",
                                     allowed, q))
    by_id = {r["id"]: r for r in kh + vh}
    fused = rrf([[str(r["id"]) for r in vh], [str(r["id"]) for r in kh]])
    ranked = sorted(by_id.values(), key=lambda r: -(fused.get(str(r["id"]), 0) + 0.3 * float(r.get("sim") or 0)))
    if not ranked:  # nothing matched: fall back to the start of each document so the model still sees content
        ranked = rows(await pool().fetch(base.format(extra="") + "AND c.idx < 3 ORDER BY c.file_id, c.idx", allowed))
    chosen: list[dict] = []
    if per_doc:
        for fid in allowed:
            chosen += [r for r in ranked if r["file_id"] == fid][:per_doc]
    for r in ranked:
        if len(chosen) >= k:
            break
        if r not in chosen:
            chosen.append(r)
    return [Passage(i + 1, r["file_id"], r["filename"], r["doc_type"], r["page"], r["section"], r["content"],
                    round(fused.get(str(r["id"]), 0), 4)) for i, r in enumerate(chosen)]


def context(passages: list[Passage], max_chars: int = 60_000) -> str:
    out, used = [], 0
    for p in passages:
        block = f"[P{p.n}] {p.filename}" + (f" ({p.where})" if p.where else "") + f"\n{p.content}"
        if used + len(block) > max_chars:
            break
        out.append(block)
        used += len(block)
    return "\n\n---\n\n".join(out)


CITE_RE = re.compile(r"\[(P\d+(?:\s*[,;]\s*P\d+)*)\]")


def validate_citations(text: str, valid: set[int]) -> tuple[str, list[int], list[str]]:
    """Drop citations to passages that weren't provided; returns (text, cited, removed)."""
    cited: set[int] = set()
    removed: list[str] = []

    def fix(m: re.Match) -> str:
        nums = [int(x) for x in re.findall(r"P(\d+)", m.group(1))]
        good = [n for n in nums if n in valid]
        removed.extend(f"P{n}" for n in nums if n not in valid)
        cited.update(good)
        return "[" + ", ".join(f"P{n}" for n in good) + "]" if good else "[unsupported]"
    return CITE_RE.sub(fix, text), sorted(cited), removed
