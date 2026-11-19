"""Structured web search (Tavily or Brave) and SSRF-guarded page fetching for research runs."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..agent.tools.web import html_to_text, is_public_url

MAX_PAGE_CHARS = 60_000


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


@dataclass
class Page:
    url: str          # final URL after redirects
    title: str
    text: str


class FetchError(Exception):
    pass


SearchFn = Callable[[str, int], Awaitable[list[SearchResult]]]
FetchFn = Callable[[str], Awaitable[Page]]


def make_search(tavily_key: str | None, brave_key: str | None) -> SearchFn | None:
    """None when no provider is configured: research must then refuse to run rather than pretend."""
    if not (tavily_key or brave_key):
        return None
    import httpx

    async def search(query: str, n: int = 8) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=25) as c:
            if tavily_key:
                r = await c.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {tavily_key}"},
                                 json={"query": query[:400], "max_results": n, "search_depth": "advanced"})
                r.raise_for_status()
                return [SearchResult(x.get("url", ""), x.get("title") or "", (x.get("content") or "")[:500])
                        for x in r.json().get("results", []) if x.get("url")]
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query[:400], "count": n},
                            headers={"X-Subscription-Token": brave_key, "Accept": "application/json"})
            r.raise_for_status()
            return [SearchResult(x.get("url", ""), x.get("title") or "", re.sub(r"<[^>]+>", "", x.get("description", ""))[:500])
                    for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    return search


async def fetch_page(url: str) -> Page:
    import httpx
    if not await asyncio.to_thread(is_public_url, url):
        raise FetchError("not a public http(s) address")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                 headers={"User-Agent": "AriaResearch/1.0 (+deep research assistant)"}) as c:
        for _ in range(5):  # every redirect hop is SSRF-checked
            r = await c.get(url)
            if r.is_redirect and r.next_request:
                url = str(r.next_request.url)
                if not await asyncio.to_thread(is_public_url, url):
                    raise FetchError("redirected to a non-public address")
                continue
            break
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype:
            text, title = _pdf_text(r.content), ""
        elif "html" in ctype or "xml" in ctype:
            title, text = html_to_text(r.text[:3_000_000])
        elif ctype.startswith("text/") or "json" in ctype:
            title, text = "", r.text
        else:
            raise FetchError(f"unsupported content type {ctype or 'unknown'}")
    return Page(url, title.strip()[:300], text[:MAX_PAGE_CHARS])


def _pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:40])
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"could not read PDF: {type(e).__name__}")
