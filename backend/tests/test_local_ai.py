"""Free local AI (Ollama): discovery, dynamic model list, model download, tool fallback. No real Ollama needed."""
import asyncio
import json

import httpx
import pytest


@pytest.fixture()
def fake_ollama(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.setenv(k, "")
    from app import local_ai
    from app.config import get_settings
    from app.llm import registry
    get_settings.cache_clear()
    state = {"models": [], "up": True, "pulled": []}

    def handler(req: httpx.Request):
        if not state["up"] or not str(req.url).startswith("http://127.0.0.1:11434"):
            raise httpx.ConnectError("refused")
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in state["models"]]})
        if req.url.path == "/api/pull":
            name = json.loads(req.content)["model"]
            state["pulled"].append(name)
            state["models"].append(name)
            lines = [{"status": "pulling manifest"}, {"status": "downloading", "total": 100, "completed": 50},
                     {"status": "downloading", "total": 100, "completed": 100}, {"status": "success"}]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        return httpx.Response(404)
    real = httpx.AsyncClient
    monkeypatch.setattr(local_ai.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    local_ai.STATE.update(root=None, models=[], checked=0.0)
    yield state
    local_ai.STATE.update(root=None, models=[], checked=0.0)
    get_settings.cache_clear()
    registry.providers.cache_clear()
    registry.available_models.cache_clear()


def test_discovery_connects_and_lists_installed_models(fake_ollama):
    from app import local_ai
    from app.config import get_settings
    from app.llm import registry

    async def go():
        st = await local_ai.discover(force=True)
        assert st["root"] == "http://127.0.0.1:11434" and st["models"] == []
        assert get_settings().ollama_base_url == "http://127.0.0.1:11434/v1"
        assert registry.available_models() == []  # Ollama running but no model yet: nothing to chat with
        fake_ollama["models"] += ["llama3.2:3b", "nomic-embed-text:latest", "qwen2.5:14b"]
        await local_ai.discover(force=True)
        ids = [m.id for m in registry.available_models()]
        assert ids == ["ollama:llama3.2:3b", "ollama:qwen2.5:14b"]  # embedding models are not chat models
        tiers = {m.id: m.tier for m in registry.available_models()}
        assert tiers["ollama:llama3.2:3b"] == "fast" and tiers["ollama:qwen2.5:14b"] == "balanced"
        assert registry.get_model(None).provider == "ollama"
    asyncio.run(go())


def test_pull_streams_progress_and_rejects_bad_names(fake_ollama):
    from app import local_ai

    async def go():
        bad = [ev async for ev in local_ai.pull("llama3; rm -rf /")]
        assert bad[0]["type"] == "error"
        evs = [ev async for ev in local_ai.pull("llama3.2:3b")]
        assert [e["percent"] for e in evs if e["type"] == "progress"] == [None, 50.0, 100.0, None]
        assert evs[-1] == {"type": "done", "models": ["llama3.2:3b"]}
        fake_ollama["up"] = False
        off = [ev async for ev in local_ai.pull("llama3.2:3b")]
        assert off[0]["type"] == "error" and "isn't running" in off[0]["message"]
    asyncio.run(go())


def test_ollama_model_without_tool_support_answers_without_tools(monkeypatch):
    from app.llm.formats import ProviderError
    from app.llm.providers import OpenAICompatProvider
    p = OpenAICompatProvider("ollama", None, "http://127.0.0.1:11434/v1", send_usage_option=False)
    bodies = []

    async def fake_post(url, headers, body, parser):
        bodies.append(dict(body))
        if "tools" in body:
            raise ProviderError("Provider returned 400: registry.ollama.ai/library/gemma:2b does not support tools", 400)
        yield {"type": "text", "text": "hi"}
        yield {"type": "done", "stop_reason": "stop", "usage": {}}
    monkeypatch.setattr(p, "_post_stream", fake_post)

    async def go():
        return [e async for e in p.stream(model="gemma:2b", system="s", messages=[],
                                         tools=[{"name": "x", "description": "d", "parameters": {"type": "object"}}])]
    evs = asyncio.run(go())
    assert evs[0]["text"] == "hi" and "tools" in bodies[0] and "tools" not in bodies[1]
