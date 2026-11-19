"""Deterministic report sections (built from stored data, never by the model) and Markdown/PDF export."""
from __future__ import annotations

import re
from datetime import datetime, timezone

STATUS_LABEL = {"corroborated": "Corroborated", "single_source": "Single source", "conflicting": "Conflicting",
                "unverified": "Unverified"}


def _cell(text: str, limit: int = 200) -> str:
    t = " ".join(str(text).split()).replace("|", "\\|")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def source_line(s: dict) -> str:
    title = s["title"] or s["domain"]
    date = str(s["fetched_at"])[:10]
    return (f"- **[S{s['n']}]** {_cell(title, 160)} — {s['domain']} — <{s['url']}> "
            f"(reliability: {s['reliability_label']}; accessed {date}"
            + ("; found while verifying a claim" if s.get("purpose") == "verification" else "") + ")")


def verification_table(claims: list[dict], important_only: bool = False) -> str:
    rows = [c for c in claims if not important_only or c["importance"] == "high"] or claims
    out = ["| # | Claim | Status | Supporting | Contradicting |", "|---|---|---|---|---|"]
    for c in rows[:60]:
        sup = ", ".join(f"S{x}" for x in c["supporting"]) or f"S{c['source']}"
        con = ", ".join(f"S{x}" for x in c["conflicting"]) or "—"
        out.append(f"| C{c['n']} | {_cell(c['text'])} | {STATUS_LABEL.get(c['status'], c['status'])} | {sup} | {con} |")
    if len(rows) > 60:
        out.append(f"\n_{len(rows) - 60} more claims are listed in the app._")
    return "\n".join(out)


def source_table(sources: list[dict], claims: list[dict]) -> str:
    out = ["| Source | Site | Reliability | Claims used | Corroborated | Conflicting |", "|---|---|---|---|---|---|"]
    for s in sources:
        mine = [c for c in claims if c["source"] == s["n"]]
        out.append(f"| S{s['n']} {_cell(s['title'] or s['domain'], 60)} | {s['domain']} | {s['reliability_label']} | "
                   f"{len(mine)} | {sum(c['status'] == 'corroborated' for c in mine)} | "
                   f"{sum(c['status'] == 'conflicting' for c in mine)} |")
    return "\n".join(out)


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def methodology(run: dict, stats: dict, all_sources: list[dict]) -> str:
    failed = [s for s in all_sources if s["status"] == "failed"]
    rejected = stats.get("quotes_rejected", 0)
    removed = len(stats.get("removed_citations") or [])
    lines = [
        f"- Question broken into {_n(len(run['plan']), 'sub-question')}; {_n(stats.get('queries', 0), 'web search', 'web searches')} "
        f"run ({stats.get('search_errors', 0)} failed), returning {_n(stats.get('candidates', 0), 'distinct page')}.",
        f"- {_n(stats.get('read', 0), 'page')} read in full"
        + (f"; {len(failed)} could not be read ({', '.join(sorted({s['domain'] for s in failed})[:8])})" if failed else "") + ".",
        f"- {_n(stats.get('claims', 0), 'claim')} extracted with verbatim quotes"
        + (f"; {rejected} more {'was' if rejected == 1 else 'were'} discarded because the quote could not be found in the page text"
           if rejected else "") + ".",
        f"- {_n(stats.get('verification_searches', 0), 'verification search', 'verification searches')} for important "
        "single-source claims.",
        f"- Citations: {_n(len(stats.get('cited', [])), 'source')} cited"
        + (f"; {_n(removed, 'citation')} to sources that were never read {'was' if removed == 1 else 'were'} removed and the "
           "statement marked as unsupported" if removed else "")
        + f"; about {round(stats.get('coverage', 0) * 100)}% of substantive paragraphs carry a citation.",
        "- Reliability ratings are domain heuristics, not fact-checks; \"corroborated\" means at least two different "
        "sites support the claim, not that it is proven.",
    ]
    return "\n".join(lines)


def assemble(run: dict, body: str, sources: list[dict], all_sources: list[dict], claims: list[dict], stats: dict) -> str:
    when = (run.get("finished_at") or datetime.now(timezone.utc))
    when = when.strftime("%d %B %Y") if hasattr(when, "strftime") else str(when)[:10]
    used = [s for s in sources if s["n"] in set(stats.get("cited", []))] or sources
    parts = [
        f"# {run['question']}",
        f"_Deep research report · {when} · {len(sources)} sources read · {len(claims)} verified claims_",
        body.strip(),
        "## Claim verification", verification_table(claims),
        "## Source comparison", source_table(sources, claims),
        "## Sources", "\n".join(source_line(s) for s in used),
    ]
    unused = [s for s in sources if s not in used]
    if unused:
        parts += ["### Also read but not cited", "\n".join(source_line(s) for s in unused)]
    parts += ["## How this research was done", methodology(run, stats, all_sources)]
    return "\n\n".join(parts) + "\n"


def executive_summary(body: str) -> str:
    m = re.search(r"#{1,3}\s*(?:executive )?summary\s*\n(.*?)(?=\n#{1,3}\s|\Z)", body, re.I | re.S)
    text = (m.group(1) if m else body).strip()
    return re.sub(r"\[S[\d,\sS;]+\]", "", text)[:1200].strip()


# ---------- PDF ----------
def _font_paths() -> tuple[str, str] | None:
    import os
    for regular, bold in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),):
        if os.path.exists(regular) and os.path.exists(bold):
            return regular, bold
    return None


def to_pdf(markdown_text: str, title: str) -> bytes:
    import markdown as md
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    html = md.markdown(markdown_text, extensions=["tables", "sane_lists"])
    html = re.sub(r"&lt;(https?://[^&]+)&gt;", r'<a href="\1">\1</a>', html)  # <url> autolinks
    pdf = FPDF(format="A4")
    pdf.set_title(title[:200])
    pdf.set_author("Aria deep research")
    pdf.set_auto_page_break(True, margin=15)
    fonts = _font_paths()
    if fonts:
        pdf.add_font("DejaVu", "", fonts[0])
        pdf.add_font("DejaVu", "B", fonts[1])
        pdf.add_font("DejaVu", "I", fonts[0])
        pdf.add_font("DejaVu", "BI", fonts[1])
        family = "DejaVu"
    else:  # core fonts are Latin-1 only
        family = "Helvetica"
        html = html.encode("latin-1", "replace").decode("latin-1")
    pdf.add_page()
    pdf.set_font(family, size=10)
    pdf.write_html(html, font_family=family, table_line_separators=True, ul_bullet_char="\u2022" if fonts else "-",
                   tag_styles={"h1": FontFace(size_pt=16, emphasis="B"), "h2": FontFace(size_pt=13, emphasis="B"),
                               "h3": FontFace(size_pt=11, emphasis="B")})
    return bytes(pdf.output())
