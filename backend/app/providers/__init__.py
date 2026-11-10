"""LLM provider registry.

``build_provider`` is the only place that chooses a concrete provider; the orchestrator receives
it as a factory so tests can inject fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

from app.config.settings import Settings, get_settings
from app.providers.base import LLMProvider, ProviderError
from app.providers.mock import MockLLMProvider

PROVIDER_NAMES = ["default", "mock", "openai", "anthropic"]

ProviderFactory = Callable[[str], LLMProvider]

_cache: dict[str, LLMProvider] = {}
_lock = Lock()


def resolve_provider_name(name: str, settings: Settings) -> str:
    return settings.llm_provider.value if name in ("", "default") else name


def build_provider(name: str, settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    name = resolve_provider_name(name, settings)
    if name == "mock":
        return MockLLMProvider()
    if name == "openai":
        from app.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(
            settings.openai_api_key, settings.openai_model,
            timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries,
        )
    if name == "anthropic":
        from app.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_api_key, settings.anthropic_model,
            effort=settings.anthropic_effort,
            refusal_fallback=settings.anthropic_refusal_fallback,
            timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries,
        )
    raise ProviderError(f"Unknown LLM provider: {name}")


def default_provider_factory(name: str) -> LLMProvider:
    """Cached per resolved provider name (SDK clients are thread-safe and reusable)."""
    resolved = resolve_provider_name(name, get_settings())
    with _lock:
        if resolved not in _cache:
            _cache[resolved] = build_provider(resolved)
        return _cache[resolved]


__all__ = [
    "LLMProvider", "ProviderError", "ProviderFactory", "PROVIDER_NAMES",
    "build_provider", "default_provider_factory", "resolve_provider_name",
]
