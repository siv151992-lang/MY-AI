"""Heuristic source reliability. Transparent on purpose: every score comes with the reason shown to the user."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

HIGH = {
    # reference, science, standards, official statistics
    "who.int", "nih.gov", "cdc.gov", "nasa.gov", "noaa.gov", "europa.eu", "un.org", "oecd.org", "worldbank.org",
    "imf.org", "nature.com", "science.org", "sciencedirect.com", "thelancet.com", "nejm.org", "bmj.com",
    "cell.com", "pnas.org", "ieee.org", "acm.org", "springer.com", "wiley.com", "jamanetwork.com", "nist.gov",
    "w3.org", "ietf.org", "iso.org", "rfc-editor.org", "ourworldindata.org", "ipcc.ch", "britannica.com",
    # wire services and newspapers of record
    "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "ft.com", "economist.com", "nytimes.com",
    "washingtonpost.com", "theguardian.com", "bloomberg.com", "wsj.com", "npr.org",
    # primary technical documentation
    "python.org", "docs.python.org", "developer.mozilla.org", "postgresql.org", "kernel.org", "rust-lang.org",
    "go.dev", "nodejs.org", "learn.microsoft.com", "docs.aws.amazon.com", "cloud.google.com", "docs.github.com",
}
MEDIUM = {
    "wikipedia.org", "arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "github.com", "stackoverflow.com",
    "investopedia.com", "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com", "cnbc.com", "forbes.com",
    "statista.com", "pewresearch.org", "gartner.com", "mckinsey.com", "hbr.org", "nationalgeographic.com",
    "scientificamerican.com", "newscientist.com", "healthline.com", "mayoclinic.org", "clevelandclinic.org",
}
LOW = {
    "medium.com", "quora.com", "reddit.com", "pinterest.com", "facebook.com", "x.com", "twitter.com",
    "tiktok.com", "instagram.com", "linkedin.com", "answers.com", "ehow.com", "blogspot.com", "wordpress.com",
    "substack.com", "youtube.com",
}


@dataclass
class Reliability:
    score: float   # 0..1
    label: str     # high | medium | low | unknown
    reason: str


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, names: set[str]) -> str | None:
    for n in names:
        if host == n or host.endswith("." + n):
            return n
    return None


def assess(url: str) -> Reliability:
    u = urlparse(url)
    host = domain_of(url)
    if not host:
        return Reliability(0.0, "low", "invalid URL")
    https = u.scheme == "https"
    if m := _matches(host, HIGH):
        r = Reliability(0.9, "high", f"{m}: established reference, research, official or primary documentation source")
    elif host.endswith((".gov", ".mil")) or ".gov." in host or host.endswith(".int"):
        r = Reliability(0.88, "high", "government or intergovernmental site")
    elif host.endswith(".edu") or ".ac." in host or ".edu." in host:
        r = Reliability(0.8, "high", "academic institution")
    elif m := _matches(host, LOW):
        r = Reliability(0.3, "low", f"{m}: user-generated or social content, unvetted")
    elif m := _matches(host, MEDIUM):
        r = Reliability(0.65, "medium", f"{m}: generally reputable but secondary, preprint or editorially variable")
    elif host.endswith(".org"):
        r = Reliability(0.55, "medium", "organisation site (check who publishes it)")
    else:
        r = Reliability(0.45, "unknown", "no reputation information for this site")
    if not https:
        r.score = max(0.0, r.score - 0.1)
        r.reason += "; not served over HTTPS"
    return r
