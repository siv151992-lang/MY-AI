"""Free local AI through Ollama: chat without any API key.

Aria looks for an Ollama server on this computer (or, from inside Docker, on the host). When one answers, it is
connected automatically and every model installed in it is offered in the model list. The owner can download a
recommended model from the chat screen with one click."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import AsyncIterator

import httpx

from .config import get_settings

log = logging.getLogger("aria.local_ai")

CANDIDATES = ("http://127.0.0.1:11434", "http://host.docker.internal:11434", "http://localhost:11434")
RECOMMENDED = [
    {"name": "llama3.2:3b", "size": "2.0 GB", "note": "Fast on most PCs (8 GB RAM). Good everyday chat."},
    {"name": "qwen2.5:7b", "size": "4.7 GB", "note": "Smarter, needs 16 GB RAM. Strong at writing and coding."},
    {"name": "llama3.2:1b", "size": "1.3 GB", "note": "Smallest. For older or low-memory PCs."},
]
ALLOWED_PULL = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,60}(:[a-z0-9._\-]{1,40})?$")
EMBEDDING_HINTS = ("embed", "bge", "minilm", "nomic")

# Discovered state (refreshed by discover()).
STATE: dict = {"root": None, "models": [], "checked": 0.0}


def _root(url: str | None) -> str | None:
    return url[:-3] if url and url.rstrip("/").endswith("/v1") else (url.rstrip("/") if url else None)


async def _tags(root: str, timeout: float = 1.5) -> list[dict] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{root}/api/tags")
        if r.status_code != 200:
            return None
        return r.json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return None


async def discover(force: bool = False) -> dict:
    """Find a running Ollama server and its installed chat models. Cheap: cached for 20 s unless forced."""
    if not force and time.monotonic() - STATE["checked"] < 20:
        return STATE
    s = get_settings()
    roots = ([_root(s.ollama_base_url)] if s.ollama_base_url else []) + list(CANDIDATES)
    found_root, models = None, []
    for root in dict.fromkeys(r for r in roots if r):
        tags = await _tags(root)
        if tags is not None:
            found_root = root
            models = [m["name"] for m in tags if not any(h in m["name"].lower() for h in EMBEDDING_HINTS)]
            break
    changed = (found_root, models) != (STATE["root"], STATE["models"])
    STATE.update(root=found_root, models=models, checked=time.monotonic())
    if changed:
        _apply()
    return STATE


def _apply() -> None:
    """Connect (or disconnect) the discovered server and refresh the model list."""
    from .llm import registry
    s = get_settings()
    if STATE["root"]:
        s.ollama_base_url = STATE["root"] + "/v1"
        log.info("Local AI (Ollama) connected at %s with models %s", STATE["root"], STATE["models"])
    registry.providers.cache_clear()
    registry.available_models.cache_clear()


def catalog_entries() -> list[dict]:
    """Model catalog entries for the installed Ollama models (replaces the static 'llama-local' entry)."""
    out = []
    for name in STATE["models"]:
        size = re.search(r":(\d+(?:\.\d+)?)b", name)
        billions = float(size.group(1)) if size else 7.0
        out.append({"id": f"ollama:{name}", "label": f"{name} (local, free)", "provider": "ollama", "model": name,
                    "vision": any(v in name for v in ("llava", "vision", "gemma3", "qwen2.5vl")), "tools": True,
                    "max_tokens": 4096, "reasoning": "scratchpad",
                    "tier": "fast" if billions < 10 else "balanced" if billions < 40 else "frontier",
                    "strengths": ["chat", "private"]})
    return out


def status() -> dict:
    return {"reachable": bool(STATE["root"]), "url": STATE["root"], "models": STATE["models"],
            "recommended": RECOMMENDED, "download_url": "https://ollama.com/download"}


async def pull(model: str) -> AsyncIterator[dict]:
    """Download a model into the local Ollama server, streaming progress events."""
    if not ALLOWED_PULL.match(model or ""):
        yield {"type": "error", "message": "Invalid model name"}
        return
    st = await discover(force=True)
    if not st["root"]:
        yield {"type": "error", "message": "Ollama isn't running on this computer."}
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=30, pool=10)) as c:
            async with c.stream("POST", f"{st['root']}/api/pull", json={"model": model, "stream": True}) as r:
                if r.status_code != 200:
                    yield {"type": "error", "message": f"Ollama answered {r.status_code}: {(await r.aread())[:200]!r}"}
                    return
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        yield {"type": "error", "message": str(ev["error"])[:300]}
                        return
                    total, done = ev.get("total"), ev.get("completed")
                    yield {"type": "progress", "status": ev.get("status", ""),
                           "percent": round(done / total * 100, 1) if total and done else None}
    except httpx.HTTPError as e:
        yield {"type": "error", "message": f"Download interrupted: {e}. Try again; Ollama resumes where it stopped."}
        return
    await discover(force=True)
    yield {"type": "done", "models": STATE["models"]}
