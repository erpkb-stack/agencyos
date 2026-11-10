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
