"""The Browser Agent's page model: open public pages, read them in parts, search them, follow links.
Every hop goes through the same SSRF guard as the research tools."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from ..agent.tools.base import Tool, ToolContext
from ..agent.tools.web import html_to_text, is_public_url

PAGE_CHARS = 6000
MAX_LINKS = 60


class _Links(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self._href, self._text = base, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self._href, self._text = urljoin(self.base, href), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())[:100]
            if urlparse(self._href).scheme in ("http", "https") and self._href not in {u for _, u in self.links}:
                self.links.append((text or self._href, self._href))
            self._href = None


def extract_links(html: str, base: str) -> list[tuple[str, str]]:
    p = _Links(base)
    p.feed(html)
    return p.links[:MAX_LINKS]


class BrowserSession:
    def __init__(self, fetch_html=None):
        self.fetch_html = fetch_html or _fetch_html
        self.url = ""
        self.title = ""
        self.text = ""
        self.links: list[tuple[str, str]] = []
        self.visited: list[str] = []

    async def open(self, url: str) -> str:
        final, html, ctype = await self.fetch_html(url)
        self.url = final
        if "html" in ctype:
            self.title, self.text = html_to_text(html)
            self.links = extract_links(html, final)
        else:
            self.title, self.text, self.links = "", html, []
        self.visited.append(final)
        return self.view(0)

    def view(self, offset: int) -> str:
        part = self.text[offset:offset + PAGE_CHARS]
        more = len(self.text) - offset - len(part)
        links = "\n".join(f"[{i}] {t} → {u}" for i, (t, u) in enumerate(self.links[:30]))
        return (f"URL: {self.url}\nTitle: {self.title}\n\n{part}"
                + (f"\n\n… {more} more characters (read_page offset={offset + len(part)})" if more > 0 else "")
                + (f"\n\nLinks on this page:\n{links}" if links and offset == 0 else ""))

    def find(self, query: str, width: int = 300) -> str:
        low, q = self.text.lower(), query.lower().strip()
        hits, start = [], 0
        while q and len(hits) < 8 and (i := low.find(q, start)) >= 0:
            hits.append(f"…{self.text[max(0, i - width // 2): i + len(q) + width // 2]}… (offset {i})")
            start = i + len(q)
        return "\n\n".join(hits) or f"'{query}' not found on {self.url}."


async def _fetch_html(url: str) -> tuple[str, str, str]:
    import httpx
    if not await asyncio.to_thread(is_public_url, url):
        raise ValueError("Refused: only public http(s) URLs can be opened.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                 headers={"User-Agent": "AriaBrowser/1.0 (+multi-agent assistant)"}) as c:
        for _ in range(5):
            r = await c.get(url)
            if r.is_redirect and r.next_request:
                url = str(r.next_request.url)
                if not await asyncio.to_thread(is_public_url, url):
                    raise ValueError("Refused: redirect to a non-public address.")
                continue
            break
        r.raise_for_status()
        return url, r.text[:3_000_000], r.headers.get("content-type", "")


def make_browser_tools(session: BrowserSession) -> list[Tool]:
    async def open_page(args: dict, ctx: ToolContext) -> str:
        return await session.open(str(args["url"]))

    async def read_page(args: dict, ctx: ToolContext) -> str:
        return session.view(int(args.get("offset") or 0)) if session.url else "No page is open."

    async def find_in_page(args: dict, ctx: ToolContext) -> str:
        return session.find(str(args["text"])) if session.url else "No page is open."

    async def follow_link(args: dict, ctx: ToolContext) -> str:
        n = int(args["index"])
        if not 0 <= n < len(session.links):
            return f"No link [{n}] on this page."
        return await session.open(session.links[n][1])

    obj = lambda p, r: {"type": "object", "properties": p, "required": r}  # noqa: E731
    return [
        Tool("open_page", "Open a public web page: returns its text (first part) and numbered links.",
             obj({"url": {"type": "string"}}, ["url"]), open_page, timeout=30),
        Tool("read_page", "Read more of the open page from a character offset.", obj({"offset": {"type": "integer"}}, ["offset"]),
             read_page, timeout=10),
        Tool("find_in_page", "Find text on the open page and show the surrounding passages.", obj({"text": {"type": "string"}}, ["text"]),
             find_in_page, timeout=10),
        Tool("follow_link", "Open link number [index] from the current page.", obj({"index": {"type": "integer"}}, ["index"]),
             follow_link, timeout=30),
    ]
