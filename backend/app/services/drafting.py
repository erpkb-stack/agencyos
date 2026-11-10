"""Shared helpers for AI drafting tasks linked to a marketing record.

Flow: the record page starts a draft-only agent task (linked by ``input.link``); when it finishes,
the user reviews the deliverables and imports the ones they want. Agents never write records.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import AgentTask
from app.models.enums import TaskPriority, TaskStatus
from app.models.identity import User
from app.services import agent_service, task_service
from app.utils.errors import AppError, NotFoundError


def start(db: Session, user: User, *, agent_key: str, title: str, instructions: str,
          context: dict[str, Any], task_type: str, entity_type: str, entity_id: int) -> AgentTask:
    agent_service.ensure_seeded(db)
    agent = agent_service.get_by_key(db, agent_key)
    if agent is None:
        raise NotFoundError(f"Agent '{agent_key}' not found")
    return task_service.create_task(
        db, user, agent_id=agent.id, title=title[:300], instructions=instructions,
        priority=TaskPriority.NORMAL, context=context, task_type=task_type,
        link={"entity_type": entity_type, "entity_id": entity_id},
    )


def linked_tasks(db: Session, entity_type: str, entity_id: int, limit: int = 20) -> list[AgentTask]:
    rows = db.scalars(select(AgentTask).where(AgentTask.parent_task_id.is_(None)).order_by(AgentTask.id.desc()).limit(500))
    out = [t for t in rows if ((t.input or {}).get("link") or {}) == {"entity_type": entity_type, "entity_id": entity_id}]
    return out[:limit]


def finished_deliverables(db: Session, task_id: int, entity_type: str, entity_id: int) -> tuple[AgentTask, list[dict[str, Any]]]:
    task = db.get(AgentTask, task_id)
    if task is None or ((task.input or {}).get("link") or {}) != {"entity_type": entity_type, "entity_id": entity_id}:
        raise NotFoundError("No drafting task with that ID belongs to this record")
    if task.status != TaskStatus.COMPLETED:
        raise AppError("The task has not finished successfully yet")
    result = (task.output or {}).get("result") or {}
    return task, list(result.get("deliverables") or [])


def split_email(deliverable: dict[str, Any]) -> tuple[str, str]:
    """'Subject: …' on the first line becomes the subject; the rest is the body."""
    content = (deliverable.get("content") or "").strip()
    match = re.match(r"^\s*subject\s*:\s*(.+?)\s*(?:\n|$)", content, re.IGNORECASE)
    if match:
        return match.group(1)[:300], content[match.end():].strip() or content
    return (deliverable.get("title") or "Untitled")[:300], content


def guess_delay(title: str, index: int) -> int:
    match = re.search(r"day\s*(\d{1,3})", title or "", re.IGNORECASE)
    if match:
        return int(match.group(1))
    return [0, 3, 7, 14, 21][index] if index < 5 else 7 * index


def task_brief(t: AgentTask) -> dict[str, Any]:
    out = task_service.task_summary(t)
    result = (t.output or {}).get("result") or {}
    out["deliverables"] = [{"index": i, "title": d.get("title"), "kind": d.get("kind")}
                           for i, d in enumerate(result.get("deliverables") or [])]
    return out
