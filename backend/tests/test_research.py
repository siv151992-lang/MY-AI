"""Deep research: reliability, evidence rules (quotes, citations, conflicts), source selection, report, PDF."""
from app.research import evidence, report
from app.research.engine import Researcher
from app.research.reliability import assess, domain_of

PAGE = ("The Eiffel Tower is 330 metres tall since a new antenna was added in 2022.\n\n"
        "It was completed in 1889 for the World’s Fair and was the tallest structure until 1930.")


def test_reliability_tiers_and_reasons():
    assert assess("https://www.who.int/news").label == "high"
    assert assess("https://data.census.gov/x").label == "high"
    assert assess("https://cs.stanford.edu/paper").label == "high"
    assert assess("https://en.wikipedia.org/wiki/X").label == "medium"
    assert assess("https://someone.medium.com/post").label == "low"
    assert assess("https://random-blog.xyz/").label == "unknown"
    plain = assess("http://www.who.int/")
    assert plain.score < assess("https://www.who.int/").score and "HTTPS" in plain.reason
    assert domain_of("https://www.Reuters.com/a") == "reuters.com"


def test_quotes_must_really_be_in_the_page():
    assert evidence.quote_in_source("the eiffel tower is 330 metres   tall", PAGE)
    assert evidence.quote_in_source("completed in 1889 for the World's Fair", PAGE)  # typographic apostrophe
    assert evidence.quote_in_source("The Eiffel Tower is 330 metres tall ... a new antenna was added in 2022", PAGE)
    assert not evidence.quote_in_source("The Eiffel Tower is 324 metres tall", PAGE)            # altered number
    assert not evidence.quote_in_source("330 metres", PAGE)                                    # too short to count
    assert not evidence.quote_in_source("tallest structure until 1930 ... built by Gustave Eiffel himself", PAGE)


def test_parse_plan_caps_and_falls_back():
    raw = '{"subquestions": [{"question": "How tall is it?", "queries": ["eiffel height", "eiffel height", "x", "y"]},' \
          ' {"question": "When built?", "queries": []}, {"question": "z"}, {"question": "Third one?"}]}'
    plan = evidence.parse_plan(raw, "Q?", max_sub=2, queries_per_sub=2)
    assert [p.question for p in plan] == ["How tall is it?", "When built?"]
    assert plan[0].queries == ["eiffel height", "x"] and plan[1].queries == ["When built?"]
    assert evidence.parse_plan("not json", "What is X?", 3, 2)[0].queries == ["What is X?"]


def test_parse_analysis_drops_unknown_ids_and_one_sided_conflicts():
    raw = '{"groups": [{"claims": ["C1", "C2", "C99"], "summary": "same"}, {"claims": ["C77"]}],' \
          ' "conflicts": [{"topic": "height", "positions": [{"summary": "330", "claims": ["C1"]}, {"summary": "324", "claims": ["C3"]}]},' \
          ' {"topic": "fake", "positions": [{"summary": "a", "claims": ["C1"]}, {"summary": "b", "claims": ["C50"]}]}]}'
    a = evidence.parse_analysis(raw, {1, 2, 3})
    assert a.groups == [{"claims": [1, 2], "summary": "same"}]
    assert len(a.conflicts) == 1 and a.conflicts[0]["topic"] == "height"


def test_corroboration_needs_two_different_sites():
    claims = [{"n": 1, "source": 1}, {"n": 2, "source": 2}, {"n": 3, "source": 3}, {"n": 4, "source": 4}]
    a = evidence.Analysis(groups=[{"claims": [1, 2]}, {"claims": [3, 4]}],
                          conflicts=[{"positions": [{"claims": [3]}, {"claims": [4]}]}])
    st = evidence.claim_statuses(claims, a, {1: "a.com", 2: "a.com", 3: "b.org", 4: "c.org"})
    assert st[1]["status"] == "single_source" and st[1]["supporting"] == [1, 2]   # same site twice
    assert st[3]["status"] == "conflicting" and st[3]["conflicting"] == [4]


def test_citations_to_unread_sources_are_removed():
    md = ("## Summary\n\n- Height is 330 m [S1].\n- It is pink [S9].\n\n"
          "A long paragraph that states a fact without any citation at all, which lowers coverage noticeably.\n\n"
          "Another fact with mixed citations [S2, S7; S1] that is long enough to be counted as substantive text.")
    out, info = evidence.validate_citations(md, {1, 2})
    assert "It is pink [unsupported: cited a source that was not read]." in out and "[S2, S1]" in out
    assert info["cited"] == [1, 2] and sorted(info["removed"]) == ["S7", "S9"]
    assert 0 < info["coverage"] < 1


def test_model_written_reference_lists_are_dropped():
    md = "## Summary\nText [S1]\n\n## References\n1. Made-up Journal (2021)\n"
    assert evidence.strip_model_sources_section(md) == "## Summary\nText [S1]"


def test_select_prefers_reliable_diverse_sources():
    r = Researcher.__new__(Researcher)
    r.plan = [evidence.SubQuestion("a", ["q"]), evidence.SubQuestion("b", ["q2"])]
    from app.research.engine import DEPTHS
    r.depth = DEPTHS["quick"]  # 1 page per site, 6 sources (over-selects to 9)
    cands = [{"url": u, "title": "", "snippet": "", "query": "q", "subqs": {s}, "best_rank": i, "hits": 1}
             for i, (u, s) in enumerate([("https://blog.medium.com/1", 0), ("https://www.nih.gov/a", 0), ("https://www.nih.gov/b", 0),
                                         ("https://example.com/x", 1), ("https://reuters.com/y", 1)])]
    chosen = [c["url"] for c in r.select(cands)]
    assert chosen[0] == "https://www.nih.gov/a" and "https://www.nih.gov/b" not in chosen
    assert chosen.index("https://reuters.com/y") < chosen.index("https://blog.medium.com/1")


def _src(n, domain, purpose="search"):
    return {"n": n, "url": f"https://{domain}/p", "title": f"Page {n}", "domain": domain, "reliability_label": "high",
            "fetched_at": "2026-11-19T10:00:00", "status": "read", "purpose": purpose}


def test_assemble_adds_data_driven_sections():
    run = {"question": "How tall is the Eiffel Tower?", "plan": [{"question": "h"}]}
    claims = [{"n": 1, "text": "It is 330 m | tall", "status": "corroborated", "supporting": [1, 2], "conflicting": [],
               "source": 1, "importance": "high"}]
    stats = {"queries": 2, "candidates": 5, "read": 2, "claims": 1, "quotes_rejected": 3, "cited": [1],
             "removed_citations": ["S9"], "coverage": 0.8}
    md = report.assemble(run, "## Executive summary\n- 330 m [S1]", [_src(1, "a.org"), _src(2, "b.org", "verification")],
                         [_src(1, "a.org"), {**_src(3, "c.org"), "status": "failed", "n": None}], claims, stats)
    assert md.startswith("# How tall is the Eiffel Tower?")
    for needle in ("## Claim verification", "| C1 | It is 330 m \\| tall | Corroborated | S1, S2 |", "## Source comparison",
                   "**[S1]** Page 1 — a.org — <https://a.org/p>", "### Also read but not cited", "found while verifying",
                   "3 more were discarded because the quote could not be found", "1 citation to sources that were never read was removed",
                   "could not be read (c.org)"):
        assert needle in md, needle
    assert report.executive_summary("## Executive summary\n- 330 m [S1]\n## Key findings\nx") == "- 330 m"


def test_pdf_export_renders_unicode_tables_and_links():
    md = "# Café — “résumé” ≥ 5\n\n## Table\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- **[S1]** Title — x.org — <https://x.org/a>\n"
    pdf = report.to_pdf(md, "Café")
    assert pdf.startswith(b"%PDF-") and len(pdf) > 2000
