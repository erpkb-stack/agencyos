"""Delegation rules, enforced in Python regardless of what the model proposes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.schemas import CEOPlan, PlannedSubtask


@dataclass
class PlanDecision:
    accepted: list[PlannedSubtask]
    rejected: list[dict[str, Any]]


def validate_plan(
    plan: CEOPlan,
    available_keys: set[str],
    *,
    chain_keys: list[str],
    depth: int,
    max_depth: int,
    max_subtasks: int,
) -> PlanDecision:
    """Accept sub-tasks that target an active specialist, are not circular, and fit the limits."""
    accepted: list[PlannedSubtask] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sub in plan.subtasks:
        key = sub.agent_key.strip()
        reason: str | None = None
        if depth > max_depth:
            reason = f"Delegation depth limit ({max_depth}) reached"
        elif key in chain_keys:
            reason = f"Circular delegation: '{key}' is already in this delegation chain"
        elif key not in available_keys:
            reason = f"Unknown or inactive agent '{key}'"
        elif key in seen:
            reason = f"'{key}' was already assigned a sub-task in this plan"
        elif len(accepted) >= max_subtasks:
            reason = f"Sub-task limit ({max_subtasks}) reached"
        if reason:
            rejected.append({"agent_key": key, "title": sub.title, "reason": reason})
        else:
            seen.add(key)
            accepted.append(sub.model_copy(update={"agent_key": key}))
    return PlanDecision(accepted=accepted, rejected=rejected)
