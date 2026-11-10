"""Agent registry persistence: seeding, listing with stats, editing and custom agents."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.agents.base import AgentConfig
from app.agents.registry import (
    BUILT_IN_AGENTS, CEO_KEY, SPECIALIST_TOOLS, SPECS_BY_KEY, TOOL_DRAFT, is_built_in,
)
from app.config.settings import Settings
from app.models.ai import Agent, AgentExecution, AgentTask
from app.models.enums import ActorType, ExecutionStatus, TaskStatus
from app.models.identity import User
from app.providers import resolve_provider_name
from app.services import audit_service
from app.utils.errors import AppError, ConflictError, NotFoundError

DEFAULT_PROVIDER = "default"  # follow LLM_PROVIDER from the server configuration


def ensure_seeded(db: Session) -> int:
    """Insert any missing built-in agents. Never overwrites Founder edits. Returns rows added."""
    existing = set(db.scalars(select(Agent.key)))
    added = 0
    for spec in BUILT_IN_AGENTS:
        if spec.key in existing:
            continue
        db.add(Agent(
            key=spec.key, name=spec.name, department=spec.department,
            description=spec.description, system_prompt=spec.system_prompt,
            capabilities=list(spec.capabilities), allowed_tools=list(spec.allowed_tools),
            model_provider=DEFAULT_PROVIDER, model_name="", temperature=0.3, is_active=True,
        ))
        added += 1
    if added:
        db.commit()
    return added


def get_agent(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError("Agent not found")
    return agent


def list_all(db: Session) -> list[Agent]:
    """CEO first, then the rest in creation order."""
    return sorted(db.scalars(select(Agent)), key=lambda a: (a.key != CEO_KEY, a.id))


def get_by_key(db: Session, key: str) -> Agent | None:
    return db.scalar(select(Agent).where(Agent.key == key))


def stats_by_agent(db: Session) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    for agent_id, total, ok, failed, tokens, cost in db.execute(
        select(
            AgentExecution.agent_id,
            func.count(),
            func.sum(case((AgentExecution.status == ExecutionStatus.SUCCEEDED, 1), else_=0)),
            func.sum(case((AgentExecution.status == ExecutionStatus.FAILED, 1), else_=0)),
            func.sum(func.coalesce(AgentExecution.prompt_tokens, 0) + func.coalesce(AgentExecution.completion_tokens, 0)),
            func.sum(AgentExecution.estimated_cost),
        ).group_by(AgentExecution.agent_id)
    ):
        stats[agent_id] = {
            "executions": total, "succeeded": int(ok or 0), "failed": int(failed or 0),
            "tokens": int(tokens or 0), "estimated_cost": f"{float(cost):.6f}" if cost is not None else None,
        }
    for agent_id, open_count in db.execute(
        select(AgentTask.agent_id, func.count())
        .where(AgentTask.status.in_([TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.RUNNING,
                                     TaskStatus.AWAITING_APPROVAL]))
        .group_by(AgentTask.agent_id)
    ):
        if agent_id is not None:
            stats.setdefault(agent_id, {})["open_tasks"] = open_count
    return stats


_EMPTY_STATS = {"executions": 0, "succeeded": 0, "failed": 0, "tokens": 0, "estimated_cost": None, "open_tasks": 0}


def serialize(agent: Agent, settings: Settings, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    provider = resolve_provider_name(agent.model_provider, settings)
    return {
        "id": agent.id,
        "key": agent.key,
        "name": agent.name,
        "department": agent.department,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "capabilities": agent.capabilities,
        "allowed_tools": agent.allowed_tools,
        "model_provider": agent.model_provider,
        "effective_provider": provider,
        "model_name": agent.model_name,
        "effective_model": agent.model_name or default_model_for(provider, settings),
        "temperature": agent.temperature,
        "is_active": agent.is_active,
        "is_built_in": is_built_in(agent.key),
        "is_ceo": agent.key == CEO_KEY,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "last_execution_at": agent.last_execution_at,
        "stats": {**_EMPTY_STATS, **(stats or {})},
    }


def default_model_for(provider: str, settings: Settings) -> str:
    return {"openai": settings.openai_model, "anthropic": settings.anthropic_model}.get(provider, "mock-1")


def to_config(agent: Agent, settings: Settings) -> AgentConfig:
    provider = resolve_provider_name(agent.model_provider, settings)
    return AgentConfig(
        id=agent.id, key=agent.key, name=agent.name, description=agent.description,
        system_prompt=agent.system_prompt, allowed_tools=list(agent.allowed_tools or []),
        provider=provider, model=agent.model_name or default_model_for(provider, settings),
        temperature=agent.temperature,
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "agent"


def create_custom(db: Session, actor: User, data: dict[str, Any]) -> Agent:
    key = f"custom_{_slug(data['name'])}"
    if get_by_key(db, key) is not None:
        raise ConflictError("An agent with this name already exists")
    tools = [t for t in data.get("allowed_tools") or [TOOL_DRAFT] if t in SPECIALIST_TOOLS]
    agent = Agent(
        key=key, name=data["name"], department=data["department"],
        description=data.get("description", ""), system_prompt=data["system_prompt"],
        capabilities=data.get("capabilities", []), allowed_tools=tools or [TOOL_DRAFT],
        model_provider=data.get("model_provider", DEFAULT_PROVIDER),
        model_name=data.get("model_name", ""), temperature=data.get("temperature", 0.3),
        is_active=True,
    )
    db.add(agent)
    db.flush()
    audit_service.record(db, "agent.created", user=actor, actor_type=ActorType.USER,
                         entity_type="agent", entity_id=agent.id, summary=f"Created agent {agent.name}")
    db.commit()
    return agent


EDITABLE = {"name", "department", "description", "system_prompt", "capabilities", "allowed_tools",
            "model_provider", "model_name", "temperature", "is_active"}


def update(db: Session, actor: User, agent: Agent, changes: dict[str, Any]) -> Agent:
    changes = {k: v for k, v in changes.items() if k in EDITABLE}
    if agent.key == CEO_KEY and changes.get("is_active") is False:
        raise AppError("The AI CEO cannot be deactivated")
    if "allowed_tools" in changes:
        # Tool permissions of built-in agents are fixed in code; custom agents pick from a safe set.
        if is_built_in(agent.key):
            raise AppError("Tool permissions of built-in agents cannot be changed")
        changes["allowed_tools"] = [t for t in changes["allowed_tools"] if t in SPECIALIST_TOOLS] or [TOOL_DRAFT]
    changed = sorted(k for k, v in changes.items() if getattr(agent, k) != v)
    for k in changed:
        setattr(agent, k, changes[k])
    if changed:
        audit_service.record(db, "agent.updated", user=actor, actor_type=ActorType.USER,
                             entity_type="agent", entity_id=agent.id,
                             summary=f"Updated {agent.name}: {', '.join(changed)}",
                             details={"fields": changed})
        db.commit()
    return agent


def reset_prompt(db: Session, actor: User, agent: Agent) -> Agent:
    spec = SPECS_BY_KEY.get(agent.key)
    if spec is None:
        raise AppError("Only built-in agents have a default prompt")
    return update(db, actor, agent, {"system_prompt": spec.system_prompt})


def active_specialists(db: Session) -> list[Agent]:
    return list(db.scalars(
        select(Agent).where(Agent.is_active.is_(True), Agent.key != CEO_KEY).order_by(Agent.id)
    ))
