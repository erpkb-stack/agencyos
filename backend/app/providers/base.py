"""Provider-neutral LLM interface.

Agents and the orchestrator only see ``LLMRequest`` / ``LLMResponse`` and ``ProviderError``.
Vendor SDKs are imported only by the concrete provider modules in this package.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class ProviderError(Exception):
    """An LLM call failed. ``retryable`` tells the orchestrator whether another attempt may help."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: list[ChatMessage]
    model: str
    schema_name: str
    json_schema: dict[str, Any]
    temperature: float = 0.3
    max_tokens: int = 16000
    # Free-form hints only the mock provider reads (e.g. the agent key); never sent to vendors.
    mock_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    provider: str
    model: str
    data: dict[str, Any]
    raw_text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost: Decimal | None = None
    notes: list[str] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str

    def default_model(self) -> str: ...

    def generate(self, request: LLMRequest) -> LLMResponse: ...


# Keywords strict structured-output modes reject or that we do not need.
_DROP_KEYS = {
    "title", "default", "examples", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "pattern", "format",
}


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Turn a Pydantic JSON schema into the strict subset both vendors accept.

    Inlines ``$ref``/``$defs``, sets ``additionalProperties: false`` and marks every property
    required. Value constraints are dropped here and enforced afterwards by Pydantic validation.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs[node["$ref"].split("/")[-1]]
            return resolve(copy.deepcopy(target))
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _DROP_KEYS or k == "$defs":
                continue
            # Keys under "properties" are field names, not schema keywords: never drop them.
            out[k] = {name: resolve(sub) for name, sub in v.items()} if k == "properties" else resolve(v)
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"].keys())
        return out

    return resolve(schema)
