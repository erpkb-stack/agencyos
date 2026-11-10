"""The approval gate: every external action (send, launch, publish, budget change) goes through here.

Modules request approval for a record (entity_type + entity_id). When a Founder/Admin decides,
the handler registered for that entity type updates the record. Nothing external is executed:
no integrations are connected, so an approval only changes what the record is allowed to do.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approvals import Approval
from app.models.enums import ActorType, ApprovalActionType, ApprovalStatus
from app.models.identity import User
from app.services import audit_service
from app.utils.errors import ConflictError
from app.utils.time import utcnow

APPROVAL_TTL = timedelta(days=7)

# entity_type -> handler(db, approval). Registered by the owning module at import time.
_handlers: dict[str, Callable[[Session, Approval], None]] = {}


def register(entity_type: str, handler: Callable[[Session, Approval], None]) -> None:
    _handlers[entity_type] = handler


def pending_for(db: Session, entity_type: str, entity_id: int) -> Approval | None:
    for a in db.scalars(select(Approval).where(Approval.status == ApprovalStatus.PENDING,
                                               Approval.task_id.is_(None))):
        p = a.payload or {}
        if p.get("entity_type") == entity_type and p.get("entity_id") == entity_id:
            return a
    return None


def history_for(db: Session, entity_type: str, entity_id: int) -> list[Approval]:
    return [a for a in db.scalars(select(Approval).where(Approval.task_id.is_(None)).order_by(Approval.id.desc()))
            if (a.payload or {}).get("entity_type") == entity_type and (a.payload or {}).get("entity_id") == entity_id]


def request(db: Session, *, user: User, action_type: ApprovalActionType, title: str, entity_type: str,
            entity_id: int, details: str, client_id: int | None = None,
            extra: dict[str, Any] | None = None) -> Approval:
    if pending_for(db, entity_type, entity_id):
        raise ConflictError("An approval request for this item is already pending")
    approval = Approval(
        action_type=action_type, title=title[:300], requested_by_user_id=user.id, client_id=client_id,
        payload={"entity_type": entity_type, "entity_id": entity_id, "details": details,
                 "requested_by": user.full_name, **(extra or {})},
        status=ApprovalStatus.PENDING, expires_at=utcnow() + APPROVAL_TTL,
    )
    db.add(approval)
    db.flush()
    audit_service.record(db, "approval.requested", user=user, actor_type=ActorType.USER,
                         entity_type="approval", entity_id=approval.id,
                         summary=f"{user.full_name} requested approval: {approval.title}")
    return approval


def settle(db: Session, approval: Approval) -> None:
    """Called after an approval is decided or expires."""
    entity_type = (approval.payload or {}).get("entity_type")
    handler = _handlers.get(entity_type) if entity_type else None
    if handler is not None:
        handler(db, approval)
