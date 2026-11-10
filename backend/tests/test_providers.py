"""Provider adapters, tested against fake SDK clients (no network, no live keys)."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from app.agents.schemas import CEOPlan, SpecialistOutput
from app.config.settings import Settings
from app.providers import build_provider
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, LLMRequest, ProviderError, strict_json_schema
from app.providers.mock import MockLLMProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.pricing import estimate_cost

VALID = {"summary": "ok", "deliverables": [], "recommendations": [], "proposed_actions": [],
         "assumptions": [], "open_questions": []}


def _request(model: str = "") -> LLMRequest:
    return LLMRequest(system="sys", messages=[ChatMessage("user", "hi")], model=model,
                      schema_name="SpecialistOutput", json_schema=SpecialistOutput.model_json_schema(),
                      temperature=0.4, max_tokens=1000)


class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.calls: list[tuple[str, dict]] = []
        outer = self

        class _Messages:
            def __init__(self, path):
                self.path = path

            def create(self, **kwargs):
                outer.calls.append((self.path, kwargs))
                if error:
                    raise error
                return response

        self.messages = _Messages("messages")
        self.beta = SimpleNamespace(messages=_Messages("beta"))


def _anthropic_response(text: str, stop: str = "end_turn", model: str = "claude-opus-5-5"):
    return SimpleNamespace(
        stop_reason=stop, model=model,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
    )


def test_anthropic_request_shape_and_usage():
    fake = FakeAnthropic(_anthropic_response(json.dumps(VALID)))
    provider = AnthropicProvider("", "claude-opus-5-5", client=fake)
    out = provider.generate(_request())

    path, kwargs = fake.calls[0]
    assert path == "beta"  # refusal fallback opted in for current models
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"] and kwargs["fallbacks"] == "default"
    assert kwargs["model"] == "claude-opus-5-5"
    assert "temperature" not in kwargs and "thinking" not in kwargs and "tool_choice" not in kwargs
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert kwargs["output_config"]["effort"] == "medium"
    assert kwargs["system"] == "sys"
    assert out.data == VALID
    assert (out.prompt_tokens, out.completion_tokens) == (1000, 500)
    assert out.estimated_cost == Decimal("0.014000")  # 1000*$4/M + 500*$20/M


def test_anthropic_older_model_skips_fallback_and_notes_fallback_model():
    fake = FakeAnthropic(_anthropic_response(json.dumps(VALID), model="claude-haiku-4-5"))
    AnthropicProvider("", "claude-haiku-4-5", client=fake).generate(_request())
    assert fake.calls[0][0] == "messages" and "fallbacks" not in fake.calls[0][1]

    fake = FakeAnthropic(_anthropic_response(json.dumps(VALID), model="claude-opus-4-8"))
    out = AnthropicProvider("", "claude-opus-5-5", client=fake).generate(_request())
    assert out.model == "claude-opus-4-8" and "fallback model" in out.notes[0]


@pytest.mark.parametrize("stop,fragment,retryable", [
    ("refusal", "declined", False), ("max_tokens", "ran out", True)])
def test_anthropic_stop_reasons(stop, fragment, retryable):
    fake = FakeAnthropic(_anthropic_response("{}", stop=stop))
    with pytest.raises(ProviderError, match=fragment) as exc:
        AnthropicProvider("", "claude-opus-5-5", client=fake).generate(_request())
    assert exc.value.retryable is retryable


def test_anthropic_errors_map_to_provider_errors():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=req), body=None)
    auth = anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=req), body=None)
    for error, retryable in ((rate, True), (auth, False), (anthropic.APIConnectionError(request=req), True)):
        with pytest.raises(ProviderError) as exc:
            AnthropicProvider("", "claude-opus-5-5", client=FakeAnthropic(error=error)).generate(_request())
        assert exc.value.retryable is retryable
        assert "bad key" not in exc.value.message or not retryable


def test_anthropic_invalid_json_is_retryable():
    fake = FakeAnthropic(_anthropic_response("not json"))
    with pytest.raises(ProviderError) as exc:
        AnthropicProvider("", "claude-opus-5-5", client=fake).generate(_request())
    assert exc.value.retryable


class FakeOpenAI:
    def __init__(self, response=None, error=None):
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if error:
                    raise error
                return response

        self.chat = SimpleNamespace(completions=_Completions())


def _openai_response(text: str, finish: str = "stop", refusal=None):
    return SimpleNamespace(
        model="gpt-4o-mini",
        choices=[SimpleNamespace(finish_reason=finish,
                                 message=SimpleNamespace(content=text, refusal=refusal))],
        usage=SimpleNamespace(prompt_tokens=2000, completion_tokens=1000),
    )


def test_openai_request_shape_and_usage():
    fake = FakeOpenAI(_openai_response(json.dumps(VALID)))
    out = OpenAIProvider("", "gpt-4o-mini", client=fake).generate(_request())
    kwargs = fake.calls[0]
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["temperature"] == 0.4
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    assert out.data == VALID
    assert out.estimated_cost == Decimal("0.000900")


def test_openai_refusal_and_errors():
    with pytest.raises(ProviderError, match="declined"):
        OpenAIProvider("", "gpt-4o-mini", client=FakeOpenAI(_openai_response("", refusal="no"))).generate(_request())
    req = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    err = openai.InternalServerError("boom", response=httpx2.Response(500, request=req), body=None)
    with pytest.raises(ProviderError) as exc:
        OpenAIProvider("", "gpt-4o-mini", client=FakeOpenAI(error=err)).generate(_request())
    assert exc.value.retryable


def test_openai_empty_balance_is_not_retried():
    req = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    # The SDK hands the inner `error` object to the exception (as seen on a live 429).
    body = {"message": "You have no credits remaining.", "type": "insufficient_quota",
            "code": "credit_balance_exhausted"}
    err = openai.RateLimitError("no credits", response=httpx2.Response(429, request=req), body=body)
    with pytest.raises(ProviderError, match="no credits") as exc:
        OpenAIProvider("", "gpt-4o-mini", client=FakeOpenAI(error=err)).generate(_request())
    assert exc.value.retryable is False


def test_missing_keys_fail_clearly_without_network():
    for name in ("openai", "anthropic"):
        with pytest.raises(ProviderError, match="not configured"):
            build_provider(name, Settings(app_env="test", llm_provider="mock"))
    assert isinstance(build_provider("default", Settings(app_env="test")), MockLLMProvider)


def test_strict_schema_inlines_refs_and_requires_every_field():
    schema = strict_json_schema(CEOPlan.model_json_schema())
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    sub = schema["properties"]["subtasks"]["items"]
    assert sub["additionalProperties"] is False
    assert set(sub["required"]) == {"agent_key", "title", "instructions", "priority"}
    assert sub["properties"]["priority"]["enum"] == ["LOW", "NORMAL", "HIGH", "URGENT"]


def test_cost_estimate_unknown_model_is_none():
    assert estimate_cost("some-new-model", 10, 10) is None
    assert estimate_cost("gpt-4o-mini", None, 10) is None


def test_mock_output_always_validates():
    mock = MockLLMProvider()
    for key in ("sales", "marketing", "funnel", "email_marketing", "advertising", "automation",
                "client_success", "finance", "analytics", "business_strategy", "custom_x"):
        req = LLMRequest(system="", messages=[ChatMessage("user", "x")], model="", schema_name="SpecialistOutput",
                         json_schema={}, mock_context={"agent_key": key, "agent_name": key,
                                                       "instructions": "do x", "can_propose": True})
        SpecialistOutput.model_validate(mock.generate(req).data)
