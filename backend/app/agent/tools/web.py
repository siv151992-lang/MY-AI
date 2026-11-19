"""Research engine tools: web search (Tavily or Brave) and safe page fetching."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

from .base import Tool, ToolContext


# ---------- HTML -> readable text (pure, testable) ----------
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip_depth:
            self.out.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


# ---------- SSRF guard ----------
def is_public_url(url: str) -> bool:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def make_web_tools(tavily_key: str | None, brave_key: str | None) -> list[Tool]:
    import httpx

    tools: list[Tool] = []

    async def web_search(args: dict, ctx: ToolContext) -> str:
        q = str(args["query"])[:400]
        n = max(1, min(int(args.get("max_results", 6)), 10))
        async with httpx.AsyncClient(timeout=20) as c:
            if tavily_key:
                r = await c.post("https://api.tavily.com/search",
                                 headers={"Authorization": f"Bearer {tavily_key}"},
                                 json={"query": q, "max_results": n, "search_depth": "advanced", "include_answer": False})
                r.raise_for_status()
                results = [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content", "")[:600]}
                           for x in r.json().get("results", [])]
            else:
                r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n},
                                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"})
                r.raise_for_status()
                results = [{"title": x.get("title"), "url": x.get("url"),
                            "snippet": re.sub(r"<[^>]+>", "", x.get("description", ""))[:600]}
                           for x in r.json().get("web", {}).get("results", [])]
        if not results:
            return "No results found. Try a different query."
        return "\n\n".join(f"[{i}] {x['title']}\nURL: {x['url']}\n{x['snippet']}" for i, x in enumerate(results, 1))

    async def fetch_url(args: dict, ctx: ToolContext) -> str:
        url = str(args["url"])
        if not await asyncio.to_thread(is_public_url, url):
            return "Refused: only public http(s) URLs can be fetched."
        async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                     headers={"User-Agent": "AriaBot/1.0 (+research assistant)"}) as c:
            for _ in range(5):  # follow redirects manually so every hop passes the SSRF check
                r = await c.get(url)
                if r.is_redirect:
                    url = str(r.next_request.url) if r.next_request else url
                    if not await asyncio.to_thread(is_public_url, url):
                        return "Refused: redirect to a non-public address."
                    continue
                break
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text[:2_000_000]
        if "html" in ctype:
            title, text = html_to_text(body)
        else:
            title, text = "", body
        return f"Title: {title}\nURL: {url}\n\n{text[:15000]}"

    if tavily_key or brave_key:
        tools.append(Tool(
            name="web_search",
            description="Search the web for current or factual information. Returns titles, URLs and snippets. "
                        "Follow up with fetch_url to read a promising page in full.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "Short, specific search query"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["query"]},
            run=web_search, timeout=30))
    tools.append(Tool(
        name="fetch_url",
        description="Fetch a public web page and return its readable text. Use to read sources found by web_search "
                    "or URLs the user provides.",
        parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        run=fetch_url, timeout=30))
    return tools
