"""Estimated API cost from token usage.

Prices are USD per 1M tokens (input, output), list prices as published by each vendor
(Anthropic table checked 2026-11). They are estimates: discounts, caching and fallback-model
billing are not modelled. Unknown models return ``None`` rather than a guess.
"""

from __future__ import annotations

from decimal import Decimal

PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    # Anthropic
    "claude-fable-5-2": (Decimal("8.00"), Decimal("40.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    # OpenAI
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
}

_MILLION = Decimal(1_000_000)


def estimate_cost(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> Decimal | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None or prompt_tokens is None or completion_tokens is None:
        return None
    cost = (Decimal(prompt_tokens) * price[0] + Decimal(completion_tokens) * price[1]) / _MILLION
    return cost.quantize(Decimal("0.000001"))
