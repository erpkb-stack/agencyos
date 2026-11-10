"""OpenAI Chat Completions provider (official ``openai`` SDK) with strict JSON-schema output."""

from __future__ import annotations

import json
import re
from typing import Any

from app.providers.base import LLMRequest, LLMResponse, ProviderError, strict_json_schema
from app.providers.pricing import estimate_cost


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        default_model: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ProviderError("OPENAI_API_KEY is not configured on the server")
            import openai

            client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._client = client
        self._default_model = default_model

    def default_model(self) -> str:
        return self._default_model

    def generate(self, request: LLMRequest) -> LLMResponse:
        import openai

        model = request.model or self._default_model
        messages = [{"role": "system", "content": request.system}] + [
            {"role": m.role, "content": m.content} for m in request.messages
        ]
        schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", request.schema_name)[:64]
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_completion_tokens=request.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": strict_json_schema(request.json_schema),
                        "strict": True,
                    },
                },
            )
        except openai.APITimeoutError as exc:
            raise ProviderError("OpenAI request timed out", retryable=True) from exc
        except openai.RateLimitError as exc:
            # 429 is also used for an empty account balance; retrying cannot fix that.
            if getattr(exc, "type", None) == "insufficient_quota" or getattr(exc, "code", None) in (
                "insufficient_quota", "credit_balance_exhausted"
            ):
                raise ProviderError(
                    "OpenAI account has no credits remaining; add credits in the OpenAI billing settings"
                ) from exc
            raise ProviderError("OpenAI rate limit reached", retryable=True) from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ProviderError("OpenAI rejected the API key or its permissions") from exc
        except openai.NotFoundError as exc:
            raise ProviderError(f"OpenAI model not found: {model}") from exc
        except openai.BadRequestError as exc:
            raise ProviderError(f"OpenAI rejected the request: {exc.message}") from exc
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"OpenAI API error ({exc.status_code})", retryable=exc.status_code >= 500
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("Could not reach the OpenAI API", retryable=True) from exc

        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderError("The model declined this request (refusal)")
        if choice.finish_reason == "length":
            raise ProviderError("The model ran out of output tokens before finishing", retryable=True)
        text = choice.message.content or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("Model returned text that is not valid JSON", retryable=True) from exc

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        served_by = getattr(response, "model", model) or model
        return LLMResponse(
            provider=self.name,
            model=served_by,
            data=data,
            raw_text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimate_cost(model, prompt_tokens, completion_tokens),
        )
