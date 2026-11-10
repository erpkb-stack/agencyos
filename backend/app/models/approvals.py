from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, TimestampMixin, str_enum
from app.models.enums import ApprovalActionType, ApprovalStatus


class Approval(IdMixin, TimestampMixin, Base):
    __tablename__ = "approvals"

    action_type: Mapped[ApprovalActionType] = mapped_column(
        str_enum(ApprovalActionType, "approval_action_type")
    )
    title: Mapped[str] = mapped_column(String(300))
    requested_by_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING, index=True
    )
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    comments: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
