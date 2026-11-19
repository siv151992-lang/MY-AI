"""Model catalog + provider registry, driven entirely by configuration."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..config import get_settings
from .providers import AnthropicProvider, LLMProvider, OpenAICompatProvider


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    provider: str
    model: str
    vision: bool
    tools: bool
    max_tokens: int


@lru_cache
def providers() -> dict[str, LLMProvider]:
    s = get_settings()
    p: dict[str, LLMProvider] = {}
    if s.anthropic_api_key:
        p["anthropic"] = AnthropicProvider(s.anthropic_api_key, s.anthropic_base_url)
    if s.openai_api_key:
        p["openai"] = OpenAICompatProvider("openai", s.openai_api_key, s.openai_base_url)
    if s.ollama_base_url:
        p["ollama"] = OpenAICompatProvider("ollama", None, s.ollama_base_url, send_usage_option=False)
    return p


@lru_cache
def available_models() -> list[ModelSpec]:
    """Only models whose provider is configured are offered to users."""
    enabled = providers()
    return [ModelSpec(**m) for m in get_settings().model_catalog() if m["provider"] in enabled]


def get_model(model_id: str | None) -> ModelSpec:
    models = available_models()
    if not models:
        raise RuntimeError("No LLM provider is configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_BASE_URL.")
    wanted = model_id or get_settings().default_model_id
    for m in models:
        if m.id == wanted:
            return m
    return models[0]


def utility_model() -> ModelSpec:
    s = get_settings()
    return get_model(s.utility_model_id or s.default_model_id)


def provider_for(spec: ModelSpec) -> LLMProvider:
    return providers()[spec.provider]


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Returns None when no embedding provider is configured (callers fall back to keyword search)."""
    s = get_settings()
    if not s.embedding_provider or not texts:
        return None
    prov = providers().get(s.embedding_provider)
    if not isinstance(prov, OpenAICompatProvider):
        return None
    vecs = await prov.embed(s.embedding_model, texts)
    if vecs and len(vecs[0]) != s.embedding_dim:
        raise RuntimeError(f"Embedding dim {len(vecs[0])} != EMBEDDING_DIM {s.embedding_dim}")
    return vecs
