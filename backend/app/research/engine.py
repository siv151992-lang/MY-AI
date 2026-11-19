"""The research pipeline. Every step is published as a progress event, and every number in the report's
methodology comes from what actually happened here."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..llm.formats import ProviderError, text_msg
from . import evidence, report, store
from .reliability import assess, domain_of
from .web import FetchError, FetchFn, SearchFn, SearchResult

log = logging.getLogger("aria.research")


@dataclass(frozen=True)
class Depth:
    subquestions: int
    queries_per_sub: int
    results_per_query: int
    max_sources: int
    per_domain: int
    verify_claims: int


DEPTHS = {
    "quick": Depth(3, 1, 6, 6, 1, 0),
    "standard": Depth(5, 2, 8, 12, 2, 3),
    "deep": Depth(7, 3, 8, 20, 2, 6),
}
MIN_PAGE_CHARS = 400
EXTRACT_CHARS = 14_000

PLAN_SYSTEM = """You plan web research. Break the user's question into {n} focused sub-questions that together answer \
it completely (definitions/background, the core facts, comparisons, recent developments, evidence quality, \
disagreements — as relevant). For each, write {q} web search queries likely to surface authoritative, primary \
sources (official sites, research papers, standards, reputable outlets). Use different phrasings, not near-duplicates.
Reply with JSON only: {{"subquestions": [{{"question": "...", "queries": ["...", "..."]}}]}}"""

EXTRACT_SYSTEM = """You extract evidence from ONE web page for a research question. Only use what this page says. \
For each relevant fact, figure, definition, finding or opinion, output:
- "claim": a precise, self-contained statement of what the page says (include numbers, dates, units, who said it)
- "quote": the exact sentence(s) from the page that support it, copied character for character (no paraphrase; \
  use "..." only to skip words inside one passage)
- "subquestion": the number of the sub-question it answers
- "importance": "high" (central to the question), "medium" or "low"
Extract at most 8 claims, prefer the most important. If the page is irrelevant, return an empty list.
Reply with JSON only: {"claims": [...]}"""

ANALYZE_SYSTEM = """You compare evidence gathered from several sources for one research sub-question. Each claim has \
an id (C#) and the source it came from (S#).
1. Group claims that state the same thing (agreement), even if worded differently. Put each claim in at most one \
group; a claim that nothing else matches can be left out.
2. Identify CONFLICTS: claims that cannot all be true (different numbers, dates, conclusions, recommendations). For \
each conflict give the positions with their claim ids, and an assessment of which position is better supported and \
why (source reliability, recency, primary vs secondary, methodology) — or say it cannot be resolved.
Do not invent claims or ids. Reply with JSON only:
{"groups": [{"claims": ["C1","C4"], "summary": "..."}],
 "conflicts": [{"topic": "...", "positions": [{"summary": "...", "claims": ["C2"]}, {"summary": "...", "claims": ["C7"]}],
                "assessment": "..."}]}"""

VERIFY_SYSTEM = """You check whether a web page supports, contradicts or does not address a specific claim. Use only \
the page. Reply with JSON only: {"verdict": "supports" | "contradicts" | "not_addressed", "quote": "exact sentence(s) \
from the page that support your verdict (empty if not_addressed)", "explanation": "one sentence"}"""

WRITE_SYSTEM = """You write a detailed, well-organised research report in Markdown, using ONLY the evidence provided \
(claims with verbatim quotes, their verification status, and identified conflicts).

Rules:
- Cite every factual statement with source markers like [S3] or [S2, S5], using only the source numbers listed. Never \
cite a source for something it does not say. Never invent sources, numbers, quotes or URLs.
- Be explicit about confidence: say when a point rests on a single source, when sources conflict, and when the \
evidence is weak, old or missing. Do not fill gaps with your own knowledge; name the gap instead.
- Prefer high-reliability and primary sources when they disagree with weaker ones, and say so.
- Do NOT write a sources/references list or a methodology section; they are appended automatically.

Structure:
## Executive summary
(5-8 bullet points with the key answers, each cited)
## Key findings
(one ### subsection per sub-question, with detailed prose; cite inline)
## Comparison
(at least one Markdown table comparing options, figures, positions or sources where the topic allows it; cite in cells)
## Conflicting information
(each conflict: the positions, which sources hold them, and which is better supported and why; "No significant \
conflicts were found." if none)
## Confidence and limitations
(what is well established, what is uncertain, what could not be found)"""


class Cancelled(Exception):
    pass


class ResearchFailed(Exception):
    """Ends the run without a report, with a user-facing explanation."""


class Researcher:
    def __init__(self, *, run: dict, writer_llm, writer_model: str, utility_llm, utility_model: str,
                 search: SearchFn, fetch: FetchFn, publish: Callable[[str, dict], Awaitable], concurrency: int = 4):
        self.run = run
        self.id = run["id"]
        self.depth = DEPTHS.get(run["depth"], DEPTHS["standard"])
        self.writer, self.writer_model = writer_llm, writer_model
        self.util, self.util_model = utility_llm, utility_model
        self.search, self.fetch = search, fetch
        self._publish = publish
        self.sem = asyncio.Semaphore(concurrency)
        self.stats: dict = {"queries": 0, "search_errors": 0, "candidates": 0, "read": 0, "failed": 0, "claims": 0,
                            "quotes_rejected": 0, "verification_searches": 0}
        self.sources: dict[int, dict] = {}      # n -> source (incl. content)
        self.seen_urls: set[str] = set()
        self.claims: list[dict] = []
        self.plan: list[evidence.SubQuestion] = []

    async def emit(self, type_: str, **data) -> None:
        await self._publish(type_, data)

    async def phase(self, status: str, text: str) -> None:
        await store.update_run(self.id, status=status, stats=self.stats)
        await self.emit("phase", status=status, text=text)

    async def _complete(self, system: str, prompt: str, *, writer: bool = False, max_tokens: int = 1500) -> str:
        llm, model = (self.writer, self.writer_model) if writer else (self.util, self.util_model)
        for attempt in range(3):
            try:
                return await llm.complete(model=model, system=system, max_tokens=max_tokens, messages=[text_msg("user", prompt)])
            except ProviderError as e:
                if not e.retryable or attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        return ""

    # ------------------------------------------------------------------ pipeline
    async def execute(self) -> None:
        await self.plan_questions()
        candidates = await self.search_all()
        await self.read_sources(candidates)
        await self.extract_all()
        analysis = await self.cross_check()
        await self.verify(analysis)
        await self.write(analysis)

    async def plan_questions(self) -> None:
        await self.phase("planning", "Breaking the question into sub-questions")
        d = self.depth
        try:
            reply = await self._complete(PLAN_SYSTEM.format(n=d.subquestions, q=d.queries_per_sub), self.run["question"], max_tokens=900)
        except ProviderError as e:
            raise ResearchFailed(f"The model could not plan the research: {e}")
        self.plan = evidence.parse_plan(reply, self.run["question"], d.subquestions, d.queries_per_sub)
        plan = [{"question": s.question, "queries": s.queries} for s in self.plan]
        await store.update_run(self.id, plan=plan)
        await self.emit("plan", subquestions=plan)

    async def search_all(self) -> list[dict]:
        await self.phase("searching", "Searching the web")
        cands: dict[str, dict] = {}
        jobs = [(i, q) for i, s in enumerate(self.plan) for q in s.queries]

        async def one(i: int, q: str) -> None:
            async with self.sem:
                try:
                    results = await self.search(q, self.depth.results_per_query)
                except Exception as e:  # noqa: BLE001 - provider/network error: recorded, never hidden
                    self.stats["search_errors"] += 1
                    await self.emit("search", query=q, ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
                    return
            self.stats["queries"] += 1
            for rank, r in enumerate(results):
                url = r.url.split("#")[0]
                if not url.startswith(("http://", "https://")):
                    continue
                c = cands.setdefault(url, {"url": url, "title": r.title, "snippet": r.snippet, "query": q,
                                           "subqs": set(), "best_rank": rank, "hits": 0})
                c["subqs"].add(i)
                c["hits"] += 1
                c["best_rank"] = min(c["best_rank"], rank)
            await self.emit("search", query=q, ok=True, results=len(results),
                            top=[{"title": r.title[:120], "domain": domain_of(r.url)} for r in results[:5]])

        await asyncio.gather(*(one(i, q) for i, q in jobs))
        self.stats["candidates"] = len(cands)
        if not cands:
            if self.stats["search_errors"] and not self.stats["queries"]:
                raise ResearchFailed("Every web search failed, so no research was performed. Check the search provider "
                                     "configuration and try again.")
            raise ResearchFailed("The web searches returned no results, so there is nothing to base a report on. "
                                 "Try rephrasing the question.")
        return self.select(list(cands.values()))

    def select(self, cands: list[dict]) -> list[dict]:
        """Prefer reliable, highly ranked, repeatedly found pages; cap pages per site; cover every sub-question."""
        for c in cands:
            rel = assess(c["url"])
            c["rel"] = rel
            c["score"] = rel.score * 0.6 + (1 / (1 + c["best_rank"])) * 0.25 + min(c["hits"], 3) / 3 * 0.15
        cands.sort(key=lambda c: -c["score"])
        chosen, per_domain = [], {}
        # first pass: the best page for each sub-question, then fill by score
        for i in range(len(self.plan)):
            for c in cands:
                if i in c["subqs"] and c not in chosen and per_domain.get(domain_of(c["url"]), 0) < self.depth.per_domain:
                    chosen.append(c)
                    per_domain[domain_of(c["url"])] = per_domain.get(domain_of(c["url"]), 0) + 1
                    break
        for c in cands:
            if len(chosen) >= int(self.depth.max_sources * 1.5):  # over-select: some pages will fail to load
                break
            d = domain_of(c["url"])
            if c not in chosen and per_domain.get(d, 0) < self.depth.per_domain:
                chosen.append(c)
                per_domain[d] = per_domain.get(d, 0) + 1
        return chosen

    async def read_sources(self, chosen: list[dict]) -> None:
        await self.phase("reading", f"Reading {len(chosen)} selected pages")
        # Fetch concurrently, then number sources in rank order so S1 is the best-ranked page that loaded.
        results = await asyncio.gather(*(self._fetch_only(c) for c in chosen))
        for c, page_or_err in zip(chosen, results):
            if len(self.sources) >= self.depth.max_sources:
                break
            await self._register(c, page_or_err)
        if not self.sources:
            raise ResearchFailed(f"None of the {len(chosen)} pages found could be read (blocked, paywalled or empty), "
                                 "so no report was written.")

    async def _fetch_only(self, c: dict):
        try:
            async with self.sem:
                page = await self.fetch(c["url"])
            if len(page.text.strip()) < MIN_PAGE_CHARS:
                raise FetchError("page has too little readable text (paywall, login or script-only page?)")
            return page
        except Exception as e:  # noqa: BLE001
            return e

    async def _register(self, c: dict, page_or_err, purpose: str = "search") -> dict | None:
        url = c["url"]
        if url in self.seen_urls:
            return None
        self.seen_urls.add(url)
        rel = c.get("rel") or assess(url)
        base = {"url": url, "domain": domain_of(url), "reliability": rel.score, "reliability_label": rel.label,
                "reliability_reason": rel.reason, "query": c.get("query", ""), "snippet": c.get("snippet", ""),
                "purpose": purpose}
        if isinstance(page_or_err, Exception):
            self.stats["failed"] += 1
            await store.add_source(self.id, **base, title=c.get("title", ""), status="failed", error=str(page_or_err)[:300])
            await self.emit("source", status="failed", url=url, domain=base["domain"], error=str(page_or_err)[:200])
            return None
        page = page_or_err
        n = len(self.sources) + 1
        src = {**base, "n": n, "title": page.title or c.get("title", ""), "content": page.text, "status": "read"}
        self.sources[n] = src
        self.stats["read"] += 1
        await store.add_source(self.id, **src)
        await self.emit("source", status="read", n=n, url=url, title=src["title"][:200], domain=src["domain"],
                        reliability=rel.label, chars=len(page.text))
        return src

    def _focus(self, text: str) -> str:
        """Long pages: keep the passages that mention the most question terms, in their original order."""
        if len(text) <= EXTRACT_CHARS:
            return text
        terms = set(re.findall(r"[a-z]{4,}", (self.run["question"] + " " + " ".join(s.question for s in self.plan)).lower()))
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        scored = sorted(range(len(paras)), key=lambda i: -len(terms & set(re.findall(r"[a-z]{4,}", paras[i].lower()))))
        keep, size = set(), 0
        for i in scored:
            if size + len(paras[i]) > EXTRACT_CHARS:
                continue
            keep.add(i)
            size += len(paras[i])
        return "\n\n".join(paras[i] for i in sorted(keep))

    async def extract_all(self) -> None:
        await self.phase("extracting", f"Extracting evidence from {len(self.sources)} sources")
        subqs = "\n".join(f"{i + 1}. {s.question}" for i, s in enumerate(self.plan))

        async def one(src: dict) -> list[dict]:
            prompt = (f"Research question: {self.run['question']}\nSub-questions:\n{subqs}\n\n"
                      f"<page url=\"{src['url']}\" title=\"{src['title'][:150]}\">\n{self._focus(src['content'])}\n</page>")
            try:
                async with self.sem:
                    reply = await self._complete(EXTRACT_SYSTEM, prompt, max_tokens=1800)
            except ProviderError as e:
                await self.emit("extract", n=src["n"], ok=False, error=str(e)[:200])
                return []
            raw = evidence.parse_claims(reply, len(self.plan))
            good = [c for c in raw if evidence.quote_in_source(c.quote, src["content"])]
            rejected = len(raw) - len(good)
            self.stats["quotes_rejected"] += rejected
            await self.emit("extract", n=src["n"], ok=True, claims=len(good), rejected=rejected)
            if not good:
                await store.set_source_status(self.id, src["n"], "irrelevant")
            return [{"subq": c.subq, "text": c.text, "quote": c.quote, "source": src["n"], "importance": c.importance}
                    for c in good]

        results = await asyncio.gather(*(one(s) for s in self.sources.values()))
        for batch in results:
            for c in batch:
                c["n"] = len(self.claims) + 1
                self.claims.append(c)
        self.stats["claims"] = len(self.claims)
        if not self.claims:
            raise ResearchFailed(f"Read {len(self.sources)} pages but none contained verifiable evidence for this question "
                                 "(every extracted statement must be backed by a quote found in the page), so no report "
                                 "was written. Try a more specific question.")
        await store.add_claims(self.id, self.claims)

    async def cross_check(self) -> evidence.Analysis:
        await self.phase("cross_checking", "Comparing sources and looking for conflicts")
        all_groups, all_conflicts = [], []
        domain_of_source = {n: s["domain"] for n, s in self.sources.items()}
        for i, sq in enumerate(self.plan):
            mine = [c for c in self.claims if c["subq"] == i]
            if len({c["source"] for c in mine}) < 2:
                continue  # nothing to compare
            listing = "\n".join(f"C{c['n']} (S{c['source']}, {self.sources[c['source']]['domain']}, "
                                f"reliability {self.sources[c['source']]['reliability_label']}): {c['text']}" for c in mine)
            try:
                reply = await self._complete(ANALYZE_SYSTEM, f"Sub-question: {sq.question}\n\nClaims:\n{listing}", max_tokens=1800)
            except ProviderError as e:
                await self.emit("analysis", subquestion=i, ok=False, error=str(e)[:200])
                continue
            a = evidence.parse_analysis(reply, {c["n"] for c in mine})
            all_groups += a.groups
            all_conflicts += [{**c, "subquestion": i} for c in a.conflicts]
            await self.emit("analysis", subquestion=i, ok=True, groups=len(a.groups), conflicts=len(a.conflicts))
        analysis = evidence.Analysis(all_groups, all_conflicts)
        await self._apply_statuses(analysis)
        return analysis

    async def _apply_statuses(self, analysis: evidence.Analysis) -> None:
        statuses = evidence.claim_statuses(self.claims, analysis, {n: s["domain"] for n, s in self.sources.items()})
        for c in self.claims:
            st = statuses[c["n"]]
            c.update(st)
            await store.update_claim(self.id, c["n"], status=st["status"], supporting=st["supporting"],
                                     conflicting=st["conflicting"])
        await store.update_run(self.id, analysis={"groups": analysis.groups, "conflicts": analysis.conflicts})

    async def verify(self, analysis: evidence.Analysis) -> None:
        targets = [c for c in self.claims if c["status"] == "single_source" and c["importance"] == "high"][: self.depth.verify_claims]
        if not targets:
            return
        await self.phase("verifying", f"Verifying {len(targets)} important single-source claim(s)")
        for c in targets:
            query = re.sub(r"[^\w\s%.,-]", " ", c["text"])[:200]
            self.stats["verification_searches"] += 1
            try:
                results: list[SearchResult] = await self.search(query, 6)
            except Exception as e:  # noqa: BLE001
                self.stats["search_errors"] += 1
                await self.emit("verify", claim=c["n"], ok=False, error=f"search failed: {str(e)[:150]}")
                continue
            origin = self.sources[c["source"]]["domain"]
            cands = [{"url": r.url.split("#")[0], "title": r.title, "snippet": r.snippet, "query": query}
                     for r in results if domain_of(r.url) != origin and r.url.split("#")[0] not in self.seen_urls][:2]
            verdicts = []
            for cand in cands:
                src = await self._register(cand, await self._fetch_only(cand), purpose="verification")
                if not src:
                    continue
                prompt = f"Claim: {c['text']}\n\n<page url=\"{src['url']}\">\n{self._focus(src['content'])}\n</page>"
                try:
                    data = evidence.extract_json(await self._complete(VERIFY_SYSTEM, prompt, max_tokens=500)) or {}
                except ProviderError:
                    continue
                verdict = str(data.get("verdict", "")).lower() if isinstance(data, dict) else ""
                quote = str(data.get("quote", "")) if isinstance(data, dict) else ""
                if verdict in ("supports", "contradicts") and evidence.quote_in_source(quote, src["content"]):
                    verdicts.append((verdict, src["n"]))
                elif verdict in ("supports", "contradicts"):
                    self.stats["quotes_rejected"] += 1
            sup = sorted({n for v, n in verdicts if v == "supports"})
            con = sorted({n for v, n in verdicts if v == "contradicts"})
            if con:
                c["status"], c["conflicting"] = "conflicting", sorted(set(c["conflicting"]) | set(con))
                c["note"] = "A verification search found a source that contradicts this."
            elif sup:
                c["status"], c["supporting"] = "corroborated", sorted(set(c["supporting"]) | set(sup))
                c["note"] = "Independently confirmed by a verification search."
            else:
                c["note"] = "A verification search found no independent confirmation."
            await store.update_claim(self.id, c["n"], status=c["status"], supporting=c["supporting"],
                                     conflicting=c["conflicting"], note=c["note"])
            await self.emit("verify", claim=c["n"], ok=True, result=c["status"], supporting=sup, contradicting=con)

    async def write(self, analysis: evidence.Analysis) -> None:
        await self.phase("writing", "Writing the report")
        used_sources = sorted({n for c in self.claims for n in c["supporting"] + c["conflicting"] + [c["source"]]})
        src_lines = "\n".join(f"S{n}: {self.sources[n]['title'][:120] or self.sources[n]['domain']} ({self.sources[n]['domain']}, "
                              f"reliability {self.sources[n]['reliability_label']})" for n in used_sources)
        by_sq = []
        for i, sq in enumerate(self.plan):
            items = [c for c in self.claims if c["subq"] == i]
            lines = "\n".join(
                f"- C{c['n']} [{c['status']}; supported by {', '.join(f'S{x}' for x in c['supporting'])}"
                + (f"; contradicted by {', '.join(f'S{x}' for x in c['conflicting'])}" if c["conflicting"] else "")
                + f"; importance {c['importance']}] {c['text']}\n  quote (S{c['source']}): \"{c['quote'][:400]}\"" for c in items)
            by_sq.append(f"### Sub-question {i + 1}: {sq.question}\n{lines or '- (no evidence found)'}")
        conflicts = "\n".join(
            f"- {c['topic']}: " + " vs ".join(f"\"{p['summary']}\" ({', '.join(f'C{x}' for x in p['claims'])})" for p in c["positions"])
            + (f" — assessment: {c['assessment']}" if c["assessment"] else "") for c in analysis.conflicts) or "- none identified"
        prompt = (f"Research question: {self.run['question']}\n\nSources you may cite:\n{src_lines}\n\nEvidence by sub-question:\n"
                  + "\n\n".join(by_sq) + f"\n\nConflicts identified:\n{conflicts}")
        try:
            body = await self._complete(WRITE_SYSTEM, prompt[:120_000], writer=True, max_tokens=6000)
        except ProviderError as e:
            raise ResearchFailed(f"The evidence was collected but the model failed while writing the report: {e}")
        body = evidence.strip_model_sources_section(body)
        body, cite = evidence.validate_citations(body, set(self.sources))
        self.stats.update(cited=cite["cited"], removed_citations=cite["removed"], coverage=cite["coverage"])
        if not cite["cited"]:
            raise ResearchFailed("The model's draft did not cite any of the sources that were read, so it was rejected "
                                 "instead of being shown as research. Try again or switch models.")
        run = {**self.run, "plan": [{"question": s.question} for s in self.plan]}
        srcs = [s for s in await store.sources(self.id) if s["status"] in ("read", "irrelevant") and s["n"]]
        all_srcs = await store.sources(self.id)
        final = report.assemble(run, body, srcs, all_srcs, await store.claims(self.id), self.stats)
        await store.update_run(self.id, report=final, summary=report.executive_summary(body), stats=self.stats)
