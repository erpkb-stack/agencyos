from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.models.enums import (
    CampaignStatus,
    CampaignType,
    ContentStatus,
    EmailKind,
    EmailMessageStatus,
    FunnelStatus,
    SuppressionReason,
)
from app.utils.time import utcnow


class Campaign(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "campaigns"

    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    campaign_type: Mapped[CampaignType] = mapped_column(str_enum(CampaignType, "campaign_type"))
    target_audience: Mapped[str | None] = mapped_column(Text)
    objectives: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[Decimal | None] = mapped_column(default=None)
    start_date: Mapped[date | None] = mapped_column(default=None)
    end_date: Mapped[date | None] = mapped_column(default=None)
    status: Mapped[CampaignStatus] = mapped_column(
        str_enum(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT, index=True
    )
    generated_content: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(default=None)


class EmailTemplate(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "email_templates"

    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[EmailKind] = mapped_column(
        str_enum(EmailKind, "email_kind"), default=EmailKind.TEMPLATE
    )
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    sequence_order: Mapped[int | None] = mapped_column(default=None)
    delay_days: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[ContentStatus] = mapped_column(
        str_enum(ContentStatus, "content_status"), default=ContentStatus.DRAFT
    )


class EmailSuppression(IdMixin, Base):
    __tablename__ = "email_suppressions"

    email: Mapped[str] = mapped_column(String(255), unique=True)
    reason: Mapped[SuppressionReason] = mapped_column(
        str_enum(SuppressionReason, "suppression_reason")
    )
    source: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class EmailMessage(IdMixin, TimestampMixin, Base):
    """One email to one recipient: rendered and frozen when approval is requested, so exactly what
    the approver reviewed is what gets sent."""

    __tablename__ = "email_messages"

    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("email_templates.id", ondelete="SET NULL"))
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id", ondelete="SET NULL"), index=True)
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id", ondelete="SET NULL"))
    step: Mapped[int] = mapped_column(default=1)
    delay_days: Mapped[int] = mapped_column(default=0)
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    status: Mapped[EmailMessageStatus] = mapped_column(
        str_enum(EmailMessageStatus, "email_message_status"), default=EmailMessageStatus.PENDING_APPROVAL, index=True
    )
    send_after: Mapped[datetime | None] = mapped_column(default=None, index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)


class Funnel(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "funnels"

    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(100))
    template_key: Mapped[str | None] = mapped_column(String(50))
    landing_page_url: Mapped[str | None] = mapped_column(String(500))
    target_conversion: Mapped[str | None] = mapped_column(String(200))
    lead_fields: Mapped[list[Any]] = mapped_column(default=list)
    call_to_action: Mapped[str | None] = mapped_column(String(200))
    form_config: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    follow_up_workflow: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    generated_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FunnelStatus] = mapped_column(
        str_enum(FunnelStatus, "funnel_status"), default=FunnelStatus.DRAFT
    )
