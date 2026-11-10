from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Column, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.models.ai import Agent
from app.models.enums import DeliverableStatus, ProjectStatus, ProjectTaskStatus
from app.utils.time import utcnow

project_agents = Table(
    "project_agents",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("agent_id", ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
)


class Project(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "completion_percent >= 0 AND completion_percent <= 100", name="completion_range"
        ),
    )

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(default=None)
    due_date: Mapped[date | None] = mapped_column(default=None)
    budget: Mapped[Decimal | None] = mapped_column(default=None)
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status"), default=ProjectStatus.PLANNED, index=True
    )
    completion_percent: Mapped[int] = mapped_column(default=0)

    agents: Mapped[list[Agent]] = relationship(secondary=project_agents)


class ProjectTask(IdMixin, TimestampMixin, Base):
    __tablename__ = "project_tasks"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProjectTaskStatus] = mapped_column(
        str_enum(ProjectTaskStatus, "project_task_status"), default=ProjectTaskStatus.TODO
    )
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assignee_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    depends_on_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_tasks.id", ondelete="SET NULL")
    )
    due_date: Mapped[date | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)


class ProjectComment(IdMixin, Base):
    __tablename__ = "project_comments"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    project_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_tasks.id", ondelete="CASCADE")
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Deliverable(IdMixin, TimestampMixin, Base):
    __tablename__ = "deliverables"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[DeliverableStatus] = mapped_column(
        str_enum(DeliverableStatus, "deliverable_status"), default=DeliverableStatus.DRAFT
    )
    url: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str | None] = mapped_column(String(500))
    due_date: Mapped[date | None] = mapped_column(default=None)
    delivered_at: Mapped[datetime | None] = mapped_column(default=None)
