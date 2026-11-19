"""Voice input. Server-side speech-to-text uses the OpenAI transcription API when an OpenAI key is configured; without
one, the web app falls back to the browser's built-in speech recognition (Chrome, Edge, Safari) and hides the
microphone where neither exists. Spoken replies use the browser's speech synthesis (no server needed)."""
from __future__ import annotations

import time

import httpx

from ..config import get_settings

MAX_BYTES = 15 * 1024 * 1024
ALLOWED = {"audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/m4a", "audio/x-m4a"}
MODEL = "whisper-1"


class VoiceError(ValueError):
    pass


def status() -> dict:
    s = get_settings()
    return {"server_transcription": bool(s.openai_api_key), "provider": "openai" if s.openai_api_key else None,
            "browser_fallback": True,
            "note": None if s.openai_api_key else
            "Server transcription needs an OpenAI key. Your browser's own speech recognition is used where available."}


async def transcribe(data: bytes, content_type: str, filename: str = "audio.webm", language: str | None = None) -> str:
    s = get_settings()
    if not s.openai_api_key:
        raise VoiceError("Server transcription needs an OpenAI key")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED:
        raise VoiceError(f"Unsupported audio type {ctype or 'unknown'}")
    if not data or len(data) > MAX_BYTES:
        raise VoiceError("Audio must be between 1 byte and 15 MB")
    form = {"model": MODEL, "response_format": "json"}
    if language and len(language) <= 5:
        form["language"] = language
    t0 = time.monotonic()
    ok, err = False, ""
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{s.openai_base_url.rstrip('/')}/audio/transcriptions",
                             headers={"authorization": f"Bearer {s.openai_api_key}"},
                             data=form, files={"file": (filename, data, ctype)})
        if r.status_code != 200:
            err = f"HTTP {r.status_code}"
            raise VoiceError(f"Transcription failed ({r.status_code}): {r.text[:200]}")
        ok = True
        return str(r.json().get("text", "")).strip()
    finally:
        from . import usage
        ctx = usage.current()
        usage.record({"user_id": ctx.get("user_id"), "feature": "voice", "provider": "openai", "model_id": f"openai:{MODEL}",
                      "input_tokens": 0, "output_tokens": 0, "latency_ms": int((time.monotonic() - t0) * 1000),
                      "ttft_ms": None, "ok": ok, "error": err})
