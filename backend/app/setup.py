"""In-app setup: connect an AI provider by pasting a key into the chat screen.

Keys entered in the app are stored in the local database (table app_settings) and applied on top of the
environment/.env settings at startup and immediately after they are saved. Only the first account created on
this installation (the owner) may change them; other users only see whether a model is connected."""
from __future__ import annotations

import logging
import re

import httpx

from . import db
from .config import get_settings

log = logging.getLogger("aria.setup")

MIGRATIONS = [
    # 009 — settings entered in the app (provider keys)
    """CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
]

# Only these settings may be changed from the app.
EDITABLE = ("anthropic_api_key", "openai_api_key", "ollama_base_url", "tavily_api_key",
            "default_model_id", "utility_model_id")

# provider -> (settings key, default chat model, utility model)
PROVIDERS = {
    "anthropic": ("anthropic_api_key", "claude-sonnet", "claude-haiku"),
    "openai": ("openai_api_key", "gpt", "gpt-mini"),
    "ollama": ("ollama_base_url", "llama-local", "llama-local"),
}
OLLAMA_URLS = ("http://127.0.0.1:11434/v1", "http://host.docker.internal:11434/v1", "http://localhost:11434/v1")
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-\.]{8,300}$")


class SetupError(ValueError):
    pass


def detect(raw: str) -> tuple[str, str]:
    """Returns (provider, cleaned value) for a pasted key; raises SetupError for anything unrecognised."""
    key = (raw or "").strip().strip("'\"").strip()
    if "=" in key and key.split("=", 1)[0].strip().upper().endswith(("API_KEY", "KEY")):
        key = key.split("=", 1)[1].strip().strip("'\"")  # someone pasted a whole ANTHROPIC_API_KEY=... line
    if key.lower() == "ollama":
        return "ollama", ""
    if key.lower().startswith("tvly-"):
        provider = "tavily"
    elif key.startswith("sk-ant-"):
        provider = "anthropic"
    elif key.startswith("sk-"):
        provider = "openai"
    else:
        raise SetupError("That doesn't look like an API key. Anthropic keys start with sk-ant-, OpenAI keys with sk-, "
                         "Tavily keys with tvly-. Or type ollama to use local models.")
    if not _KEY_RE.match(key):
        raise SetupError("That key contains unexpected characters. Copy it again and paste only the key itself.")
    return provider, key


async def _get(url: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        return await client.get(url, headers=headers)


async def verify(provider: str, value: str) -> str:
    """Checks the key with a free, read-only request. Returns the value to store (for Ollama: the working URL).
    Raises SetupError when the provider rejects the key or cannot be reached."""
    s = get_settings()
    if provider == "ollama":
        for url in OLLAMA_URLS:
            try:
                if (await _get(f"{url}/models", {})).status_code < 500:
                    return url
            except httpx.HTTPError:
                continue
        raise SetupError("Ollama isn't running on this computer. Install it from https://ollama.com, run "
                         "`ollama pull llama3.1`, then try again.")
    if provider == "anthropic":
        url, headers = f"{s.anthropic_base_url.rstrip('/')}/v1/models", {"x-api-key": value, "anthropic-version": "2023-06-01"}
    elif provider == "openai":
        url, headers = f"{s.openai_base_url.rstrip('/')}/models", {"Authorization": f"Bearer {value}"}
    else:  # tavily has no free check endpoint; the format check is enough
        return value
    try:
        r = await _get(url, headers)
    except httpx.HTTPError:
        raise SetupError(f"Couldn't reach {provider.title()} to check the key. Check your internet connection and try again.")
    if r.status_code in (401, 403):
        raise SetupError(f"{provider.title()} rejected this key. Check that you copied all of it and that it is still active.")
    if r.status_code >= 400 and r.status_code != 429:  # 429 = valid key, just rate limited
        raise SetupError(f"{provider.title()} answered with an error ({r.status_code}). Try again in a minute.")
    return value


def apply(values: dict[str, str | None]) -> None:
    """Applies settings to the running process and refreshes the provider/model catalog."""
    from .llm import registry
    s = get_settings()
    for k, v in values.items():
        if k in EDITABLE:
            setattr(s, k, v or None)
    registry.providers.cache_clear()
    registry.available_models.cache_clear()


async def load() -> None:
    """Startup: apply the settings saved in the app on top of the environment."""
    rows = await db.pool().fetch("SELECT key, value FROM app_settings")
    values = {r["key"]: r["value"] for r in rows if r["key"] in EDITABLE}
    if values:
        apply(values)
        log.info("Applied settings saved in the app: %s", sorted(values))


async def save(values: dict[str, str | None], user_id: str) -> None:
    async with db.pool().acquire() as c, c.transaction():
        for k, v in values.items():
            if k not in EDITABLE:
                raise SetupError(f"{k} can't be changed from the app")
            if v:
                await c.execute("INSERT INTO app_settings (key, value, updated_by) VALUES ($1, $2, $3) "
                                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, "
                                "updated_at=now()", k, v, user_id)
            else:
                await c.execute("DELETE FROM app_settings WHERE key=$1", k)
    apply(values)


async def saved_keys() -> set[str]:
    return {r["key"] for r in await db.pool().fetch("SELECT key FROM app_settings")}


async def is_owner(user_id: str) -> bool:
    first = await db.pool().fetchval("SELECT id FROM users ORDER BY created_at, id LIMIT 1")
    return first is not None and str(first) == str(user_id)


def connected() -> list[str]:
    from .llm import registry
    return sorted(registry.providers())


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return value if value.startswith("http") else f"{value[:7]}…{value[-4:]}"
