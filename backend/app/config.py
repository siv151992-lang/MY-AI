"""Application settings. Every secret comes from the environment; nothing is hardcoded."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    database_url: str = "postgresql://aria:aria@localhost:5432/aria"
    jwt_secret: str = Field(..., min_length=32)  # required: refuse to start without a strong secret
    jwt_expire_hours: int = 24 * 7
    cookie_secure: bool = False  # set True behind HTTPS
    cors_origins: str = "http://localhost:3000"
    upload_dir: Path = Path("/data/uploads")
    max_upload_mb: int = 25
    allow_signup: bool = True

    # LLM providers (any subset may be configured)
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str | None = None  # e.g. http://host.docker.internal:11434/v1

    # Model catalog: JSON file describing selectable models
    models_file: Path = Path(__file__).parent.parent / "models.json"
    default_model_id: str | None = None
    utility_model_id: str | None = None  # cheap model for titles, memory extraction, planning, verification

    # Embeddings (OpenAI-compatible /embeddings endpoint). Leave empty to use keyword search only.
    embedding_provider: str | None = None  # "openai" | "ollama"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    disable_pgvector: bool = False  # force full-text-only search even if pgvector is installed

    # Tools
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    sandbox_url: str | None = "http://sandbox:8800"  # "off" (or empty) disables code execution
    sandbox_token: str | None = None

    # Action tools for the autonomous agent (each is enabled only when configured)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    slack_webhook_url: str | None = None
    enable_http_tool: bool = True

    # Autonomous runs
    autonomy_max_plan_steps: int = 12
    autonomy_step_retries: int = 2          # extra attempts per step before re-planning
    autonomy_max_replans: int = 2
    autonomy_calls_per_step: int = 10        # model calls (tool rounds) per step attempt
    autonomy_approval_timeout_min: int = 30
    autonomy_max_concurrent_runs: int = 3    # per user

    # Coding workspace
    workspace_root: Path = Path("/data/workspaces")
    workspace_max_agent_steps: int = 40      # tool rounds per coding turn
    workspace_approval_timeout_min: int = 15
    workspace_import_max_mb: int = 50

    # Agent
    max_agent_steps: int = 8
    max_revisions: int = 1
    context_token_budget: int = 60_000
    rate_limit_per_minute: int = 20

    @field_validator("sandbox_url", mode="before")
    @classmethod
    def _sandbox_off(cls, v):
        return None if v is None or str(v).strip().lower() in ("", "off", "none", "false", "0", "disabled") else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def model_catalog(self) -> list[dict]:
        return json.loads(self.models_file.read_text())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
