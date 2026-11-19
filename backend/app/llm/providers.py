"""Pluggable LLM providers. Add a provider by subclassing LLMProvider and registering it in registry.py."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from .formats import (AnthropicStreamParser, OpenAIStreamParser, ProviderError, to_anthropic, to_openai,
                      tools_to_anthropic, tools_to_openai)

TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 529}


class LLMProvider:
    name = "base"

    async def stream(self, *, model: str, system: str, messages: list[dict], tools: list[dict] | None = None,
                     max_tokens: int = 4096, temperature: float | None = None) -> AsyncIterator[dict]:
        raise NotImplementedError
        yield {}  # pragma: no cover

    async def complete(self, *, model: str, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        """Non-streaming convenience used by utility engines (planner, verifier, memory, titles)."""
        out = []
        async for ev in self.stream(model=model, system=system, messages=messages, max_tokens=max_tokens,
                                    temperature=0.2):
            if ev["type"] == "text":
                out.append(ev["text"])
        return "".join(out)

    async def _post_stream(self, url: str, headers: dict, body: dict, parser) -> AsyncIterator[dict]:
        attempt = 0
        while True:
            attempt += 1
            emitted = False
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    async with client.stream("POST", url, headers=headers, json=body) as resp:
                        if resp.status_code != 200:
                            text = (await resp.aread()).decode(errors="replace")[:500]
                            raise ProviderError(_error_message(text, resp.status_code), resp.status_code,
                                                retryable=resp.status_code in RETRY_STATUSES)
                        async for line in resp.aiter_lines():
                            for ev in parser.feed(line):
                                emitted = True
                                yield ev
                return
            except (httpx.TransportError, ProviderError) as e:
                retryable = isinstance(e, httpx.TransportError) or getattr(e, "retryable", False)
                if emitted or not retryable or attempt >= 3:
                    if isinstance(e, ProviderError):
                        raise
                    raise ProviderError(f"Connection to {self.name} failed: {e}") from e
                await asyncio.sleep(1.5 * attempt)
                parser = type(parser)()


def _error_message(text: str, status: int) -> str:
    try:
        data = json.loads(text)
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:
        msg = text
    return f"Provider returned {status}: {msg}"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None):
        body: dict = {"model": model, "system": system, "messages": to_anthropic(messages),
                      "max_tokens": max_tokens, "stream": True}
        if tools:
            body["tools"] = tools_to_anthropic(tools)
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        async for ev in self._post_stream(f"{self.base_url}/v1/messages", headers, body, AnthropicStreamParser()):
            yield ev


class OpenAICompatProvider(LLMProvider):
    """OpenAI, Ollama, Groq, OpenRouter, vLLM, LM Studio... anything speaking /chat/completions."""

    def __init__(self, name: str, api_key: str | None, base_url: str, send_usage_option: bool = True):
        self.name, self.api_key, self.base_url = name, api_key, base_url.rstrip("/")
        self.send_usage_option = send_usage_option

    def _headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def stream(self, *, model, system, messages, tools=None, max_tokens=4096, temperature=None):
        body: dict = {"model": model, "messages": to_openai(system, messages), "stream": True,
                      "max_completion_tokens" if self.name == "openai" else "max_tokens": max_tokens}
        if self.send_usage_option:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools_to_openai(tools)
        if temperature is not None:
            body["temperature"] = temperature
        async for ev in self._post_stream(f"{self.base_url}/chat/completions", self._headers(), body,
                                          OpenAIStreamParser()):
            yield ev

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for i in range(0, len(texts), 64):
                resp = await client.post(f"{self.base_url}/embeddings", headers=self._headers(),
                                         json={"model": model, "input": texts[i:i + 64]})
                if resp.status_code != 200:
                    raise ProviderError(_error_message(resp.text, resp.status_code), resp.status_code)
                out.extend(d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"]))
        return out
