"""Provider-neutral message format, converters and streaming parsers.

Pure Python (no network imports) so it can be unit-tested in isolation.

Neutral message:  {"role": "user"|"assistant", "content": [part, ...]}
Parts:
  {"type": "text", "text": str}
  {"type": "image", "media_type": str, "data": base64-str}
  {"type": "tool_call", "id": str, "name": str, "input": dict}          (assistant)
  {"type": "tool_result", "tool_call_id": str, "content": str, "is_error": bool}  (user)
  {"type": "thinking", "thinking": str, "signature": str}      (assistant; private, provider-native)
  {"type": "redacted_thinking", "data": str}                    (assistant; private, provider-native)
Stream events:
  {"type": "text", "text": str}
  {"type": "tool_call", "id": str, "name": str, "input": dict}
  {"type": "done", "stop_reason": str, "usage": {"input_tokens": int, "output_tokens": int}}
  {"type": "thinking_start"}                               (progress signal only — no content)
  {"type": "thinking_block", "part": <thinking part>}      (private; kept server-side for tool loops)
"""
from __future__ import annotations

import json
from typing import Any, Iterator


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def text_msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _loads(s: str) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {"value": v}
    except json.JSONDecodeError:
        return {"_raw": s}


# ---------------------------------------------------------------- Anthropic
def to_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        blocks: list[dict] = []
        for p in m["content"]:
            t = p["type"]
            if t == "text":
                if p["text"]:
                    blocks.append({"type": "text", "text": p["text"]})
            elif t == "image":
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": p["media_type"], "data": p["data"]}})
            elif t == "tool_call":
                blocks.append({"type": "tool_use", "id": p["id"], "name": p["name"], "input": p["input"]})
            elif t == "tool_result":
                blocks.append({"type": "tool_result", "tool_use_id": p["tool_call_id"], "content": p["content"],
                               "is_error": bool(p.get("is_error"))})
            elif t == "thinking":
                blocks.append({"type": "thinking", "thinking": p["thinking"], "signature": p["signature"]})
            elif t == "redacted_thinking":
                blocks.append({"type": "redacted_thinking", "data": p["data"]})
        if not blocks:
            blocks = [{"type": "text", "text": "(empty)"}]
        # Anthropic requires strictly alternating roles: merge consecutive same-role messages.
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": m["role"], "content": blocks})
    return out


def tools_to_anthropic(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]


class AnthropicStreamParser:
    """Feed raw SSE lines; yields neutral stream events."""

    def __init__(self) -> None:
        self.event: str | None = None
        self.blocks: dict[int, dict] = {}
        self.stop_reason = "end_turn"
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def feed(self, line: str) -> Iterator[dict]:
        line = line.rstrip("\r")
        if line.startswith("event:"):
            self.event = line[6:].strip()
            return
        if not line.startswith("data:"):
            return
        data = json.loads(line[5:].strip())
        typ = data.get("type", self.event)
        if typ == "message_start":
            u = data.get("message", {}).get("usage", {})
            self.usage["input_tokens"] = u.get("input_tokens", 0)
        elif typ == "content_block_start":
            cb = data["content_block"]
            self.blocks[data["index"]] = {"type": cb["type"], "id": cb.get("id"), "name": cb.get("name"), "json": "",
                                          "thinking": cb.get("thinking", ""), "signature": cb.get("signature", ""),
                                          "data": cb.get("data", "")}
            if cb["type"] in ("thinking", "redacted_thinking"):
                yield {"type": "thinking_start"}
        elif typ == "content_block_delta":
            d = data["delta"]
            if d["type"] == "text_delta":
                yield {"type": "text", "text": d["text"]}
            elif d["type"] == "input_json_delta":
                self.blocks[data["index"]]["json"] += d.get("partial_json", "")
            elif d["type"] == "thinking_delta":
                self.blocks[data["index"]]["thinking"] += d.get("thinking", "")
            elif d["type"] == "signature_delta":
                self.blocks[data["index"]]["signature"] += d.get("signature", "")
        elif typ == "content_block_stop":
            b = self.blocks.pop(data["index"], None)
            if b and b["type"] == "tool_use":
                yield {"type": "tool_call", "id": b["id"], "name": b["name"], "input": _loads(b["json"])}
            elif b and b["type"] == "thinking":
                yield {"type": "thinking_block",
                       "part": {"type": "thinking", "thinking": b["thinking"], "signature": b["signature"]}}
            elif b and b["type"] == "redacted_thinking":
                yield {"type": "thinking_block", "part": {"type": "redacted_thinking", "data": b["data"]}}
        elif typ == "message_delta":
            self.stop_reason = data.get("delta", {}).get("stop_reason") or self.stop_reason
            self.usage["output_tokens"] = data.get("usage", {}).get("output_tokens", self.usage["output_tokens"])
        elif typ == "message_stop":
            yield {"type": "done", "stop_reason": self.stop_reason, "usage": dict(self.usage)}
        elif typ == "error":
            err = data.get("error", {})
            raise ProviderError(err.get("message", "Anthropic stream error"),
                                retryable=err.get("type") in ("overloaded_error", "api_error"))


# ---------------------------------------------------------------- OpenAI-compatible
def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        texts_images: list[dict] = []
        tool_calls: list[dict] = []
        for p in m["content"]:
            t = p["type"]
            if t == "text" and p["text"]:
                texts_images.append({"type": "text", "text": p["text"]})
            elif t == "image":
                texts_images.append({"type": "image_url",
                                     "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}})
            elif t == "tool_call":
                tool_calls.append({"id": p["id"], "type": "function",
                                   "function": {"name": p["name"], "arguments": json.dumps(p["input"])}})
            elif t == "tool_result":
                out.append({"role": "tool", "tool_call_id": p["tool_call_id"], "content": p["content"]})
        if m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant",
                                   "content": "".join(x["text"] for x in texts_images if x["type"] == "text") or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif texts_images:
            only_text = all(x["type"] == "text" for x in texts_images)
            out.append({"role": "user",
                        "content": "\n\n".join(x["text"] for x in texts_images) if only_text else texts_images})
    return out


def tools_to_openai(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["parameters"]}} for t in tools]


class OpenAIStreamParser:
    def __init__(self) -> None:
        self.calls: dict[int, dict] = {}
        self.finish = "stop"
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.finished = False

    def _flush(self) -> Iterator[dict]:
        for i in sorted(self.calls):
            c = self.calls[i]
            yield {"type": "tool_call", "id": c["id"] or f"call_{i}", "name": c["name"], "input": _loads(c["args"])}
        self.calls.clear()

    def feed(self, line: str) -> Iterator[dict]:
        line = line.strip()
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if payload == "[DONE]":
            if not self.finished:
                yield from self._flush()
                self.finished = True
                reason = "tool_use" if self.finish == "tool_calls" else ("max_tokens" if self.finish == "length" else "end_turn")
                yield {"type": "done", "stop_reason": reason, "usage": dict(self.usage)}
            return
        data = json.loads(payload)
        if "error" in data:
            raise ProviderError(str(data["error"].get("message", data["error"])))
        if data.get("usage"):
            self.usage = {"input_tokens": data["usage"].get("prompt_tokens", 0),
                          "output_tokens": data["usage"].get("completion_tokens", 0)}
        for ch in data.get("choices", []):
            delta = ch.get("delta") or {}
            if delta.get("content"):
                yield {"type": "text", "text": delta["content"]}
            for tc in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
