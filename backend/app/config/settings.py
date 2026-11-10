"""Centralized application configuration.

All settings come from environment variables or ``agencyos/.env``. Nothing secret is hardcoded.
"""

from __future__ import annotations

import secrets
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# agencyos/ (project root) — backend/app/config/settings.py -> parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
STORAGE_DIR = PROJECT_ROOT / "storage"

APP_VERSION = "0.1.0"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AgencyOS"
    app_env: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: str = "INFO"

    # Security
    secret_key: str = Field(default="", repr=False)
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)
    cookie_secure: bool | None = None  # defaults to True in production
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)
    api_rate_limit_per_minute: int = Field(default=300, ge=1)
    enable_demo_login: bool = False
    # Interactive API docs (/docs, /openapi.json). Default: on except in production.
    enable_api_docs: bool | None = None
    # Largest request body accepted (bytes). CSV imports are capped separately at 2 MB.
    max_request_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024, le=100 * 1024 * 1024)
    # Failed logins allowed per account in the window before that account is throttled.
    login_account_limit: int = Field(default=5, ge=1, le=100)
    login_account_window_seconds: int = Field(default=900, ge=60, le=86400)

    # Database
    database_url: str = f"sqlite:///{DATA_DIR / 'agencyos.db'}"
    database_echo: bool = False

    # LLM providers. Keys stay server-side and are never returned by the API.
    llm_provider: LLMProviderName = LLMProviderName.MOCK
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = Field(default="medium", pattern="^(low|medium|high|xhigh|max)$")
    anthropic_refusal_fallback: bool = True
    llm_timeout_seconds: int = Field(default=120, ge=1, le=600)
    llm_max_tokens: int = Field(default=16000, ge=256, le=64000)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Agency identity used in email drafts (CAN-SPAM: sender + postal address) and unsubscribe links
    agency_name: str = ""
    agency_address: str = ""
    sender_name: str = ""
    sender_email: str = ""
    public_base_url: str = "http://localhost:8000"

    # Orchestrator limits (enforced in Python, not by the model)
    orchestrator_max_subtasks: int = Field(default=5, ge=1, le=10)
    orchestrator_max_depth: int = Field(default=2, ge=1, le=3)
    # Specialists of one CEO request that may run at the same time.
    orchestrator_parallel_agents: int = Field(default=3, ge=1, le=8)
    # Queued requests processed at the same time by a worker.
    task_workers: int = Field(default=2, ge=1, le=8)
    # Run the queue worker inside the web process (default). Set false and run
    # `python -m app.cli worker` separately to keep agent work out of the web process.
    run_worker_in_process: bool = True
    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=60)
    # Estimated AI spend limits in USD (0 = no limit).
    ai_cost_limit_per_request: Decimal = Field(default=Decimal("2.00"), ge=0, max_digits=10, decimal_places=2)
    ai_cost_limit_per_month: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_environment_rules(self) -> "Settings":
        if self.app_env == Environment.PRODUCTION:
            if self.enable_demo_login:
                raise ValueError("ENABLE_DEMO_LOGIN must not be true when APP_ENV=production")
            if len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set (>= 32 characters) in production")
            if self.debug:
                raise ValueError("DEBUG must be false in production")
        elif not self.secret_key:
            # Development/test: an ephemeral key is acceptable; sessions reset on restart.
            self.secret_key = secrets.token_urlsafe(48)
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def demo_login_enabled(self) -> bool:
        # Double gate: flag AND development environment.
        return self.enable_demo_login and self.app_env == Environment.DEVELOPMENT

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def database_backend(self) -> str:
        return self.database_url.split(":", 1)[0].split("+", 1)[0]

    @property
    def api_docs_enabled(self) -> bool:
        return (not self.is_production) if self.enable_api_docs is None else self.enable_api_docs

    def production_warnings(self) -> list[str]:
        """Non-fatal issues worth fixing before real use. Shown in Settings and logged at start."""
        warnings: list[str] = []
        if self.is_production:
            if self.database_backend == "sqlite":
                warnings.append("SQLite is fine for one person on one machine; use PostgreSQL for a shared server.")
            if not self.public_base_url.startswith("https://"):
                warnings.append("PUBLIC_BASE_URL should use https:// so unsubscribe links are secure.")
            if not self.secure_cookies:
                warnings.append("COOKIE_SECURE is off; session cookies would be sent over plain HTTP.")
        provider = self.llm_provider.value
        if provider == "openai" and not self.openai_api_key:
            warnings.append("LLM_PROVIDER=openai but OPENAI_API_KEY is not set; agent tasks will fail.")
        if provider == "anthropic" and not self.anthropic_api_key:
            warnings.append("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set; agent tasks will fail.")
        if not (self.agency_name and self.agency_address and self.sender_name):
            warnings.append("Agency identity (AGENCY_NAME, AGENCY_ADDRESS, SENDER_NAME) is incomplete; email drafts show [missing: …].")
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
