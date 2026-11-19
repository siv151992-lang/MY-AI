"""Deep Research mode.

Pipeline (engine.py), one background task per run, every step streamed as a progress event:

  plan       split the question into sub-questions and search queries
  search     run every query against a real web search provider
  select     rank candidate pages by source reliability, relevance and diversity
  read       fetch pages (SSRF-guarded) and extract their text
  extract    pull claims from each page, each with a verbatim quote; claims whose quote is not
             actually in the page text are discarded (no fabricated evidence)
  cross-check group claims across sources: agreement, conflicts, single-source claims
  verify     targeted searches for important single-source claims
  write      the model writes the report citing [S#] markers; markers that don't point at a source
             that was actually fetched are removed; the sources list, the verification table and the
             methodology are generated from the database, not by the model

Modules: reliability.py, evidence.py (pure rules), web.py (search + fetch), store.py, engine.py,
report.py (Markdown / PDF), manager.py.
"""
