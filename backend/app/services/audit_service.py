from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import ActorType
from app.models.identity import AuditLog, User


def record(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    actor_type: ActorType = ActorType.USER,
    actor_label: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Add an audit row to the current transaction (caller commits)."""
    entry = AuditLog(
        action=action,
        user_id=user.id if user else None,
        actor_type=actor_type,
        actor_label=actor_label or (user.full_name if user else None),
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    return entry
