"""Anthropic Messages API provider (official ``anthropic`` SDK).

Structured output uses ``output_config.format`` (JSON schema), which current models support;
forced ``tool_choice`` is rejected on Claude Opus 5.5 / Sonnet 5.5, so it is not used. Sampling
parameters (temperature) are not sent: current Claude models reject them. Thinking is left at
the model default (adaptive) and depth is controlled with ``effort``.
"""

from __future__ import annotations

import json
from typing import Any

from app.providers.base import LLMRequest, LLMResponse, ProviderError, strict_json_schema
from app.providers.pricing import estimate_cost

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        default_model: str,
        *,
        effort: str = "medium",
        refusal_fallback: bool = True,
        timeout: float = 120.0,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not configured on the server")
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._client = client
        self._default_model = default_model
        self._effort = effort
        self._refusal_fallback = refusal_fallback

    def default_model(self) -> str:
        return self._default_model

    def generate(self, request: LLMRequest) -> LLMResponse:
        import anthropic

        model = request.model or self._default_model
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": strict_json_schema(request.json_schema)},
            },
        }
        use_fallback = self._refusal_fallback and model in _FALLBACK_MODELS
        try:
            if use_fallback:
                response = self._client.beta.messages.create(
                    **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise ProviderError("Anthropic request timed out", retryable=True) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached", retryable=True) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderError("Anthropic rejected the API key or its permissions") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"Anthropic model not found: {model}") from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(f"Anthropic rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"Anthropic API error ({exc.status_code})", retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Could not reach the Anthropic API", retryable=True) from exc

        notes: list[str] = []
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ProviderError("The model declined this request (safety refusal)")
        if stop == "max_tokens":
            raise ProviderError("The model ran out of output tokens before finishing", retryable=True)
        served_by = getattr(response, "model", model) or model
        if served_by != model:
            notes.append(f"Served by fallback model {served_by}")

        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Model returned text that is not valid JSON", retryable=True) from exc

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        return LLMResponse(
            provider=self.name,
            model=served_by,
            data=data,
            raw_text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimate_cost(served_by, prompt_tokens, completion_tokens),
            notes=notes,
        )
