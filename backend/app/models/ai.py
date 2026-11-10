from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.models.enums import ExecutionStatus, MessageRole, TaskPriority, TaskStatus
from app.utils.time import utcnow


class Agent(IdMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    capabilities: Mapped[list[Any]] = mapped_column(default=list)
    allowed_tools: Mapped[list[Any]] = mapped_column(default=list)
    model_provider: Mapped[str] = mapped_column(String(30), default="mock")
    model_name: Mapped[str] = mapped_column(String(100), default="mock-1")
    temperature: Mapped[float] = mapped_column(default=0.3)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_execution_at: Mapped[datetime | None] = mapped_column(default=None)


class Conversation(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "conversations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(IdMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    meta: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentTask(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_tasks"

    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300))
    task_type: Mapped[str] = mapped_column(String(60), default="general")
    priority: Mapped[TaskPriority] = mapped_column(
        str_enum(TaskPriority, "task_priority"), default=TaskPriority.NORMAL
    )
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus, "task_status"), default=TaskStatus.PENDING, index=True
    )
    input: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    output: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=2)
    depth: Mapped[int] = mapped_column(default=0)
    requires_approval: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    agent: Mapped[Agent | None] = relationship()


class AgentExecution(IdMixin, Base):
    __tablename__ = "agent_executions"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        str_enum(ExecutionStatus, "execution_status"), default=ExecutionStatus.RUNNING
    )
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    input: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    output: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(default=None)
    completion_tokens: Mapped[int | None] = mapped_column(default=None)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    agent: Mapped[Agent] = relationship()
