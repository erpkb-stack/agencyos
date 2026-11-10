from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.models.enums import ContractStatus, SalesStage, WebsiteCheckStatus
from app.utils.time import utcnow


class Prospect(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "prospects"
    __table_args__ = (
        CheckConstraint("lead_score >= 0 AND lead_score <= 100", name="lead_score_range"),
    )

    company_name: Mapped[str] = mapped_column(String(200), index=True)
    website: Mapped[str | None] = mapped_column(String(300))
    normalized_domain: Mapped[str | None] = mapped_column(String(200), index=True)
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(100), index=True)
    state: Mapped[str | None] = mapped_column(String(50))
    zip_code: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_title: Mapped[str | None] = mapped_column(String(120))
    lead_source: Mapped[str | None] = mapped_column(String(100))
    observations: Mapped[str | None] = mapped_column(Text)
    opportunity: Mapped[str | None] = mapped_column(Text)
    lead_score: Mapped[int] = mapped_column(default=0)
    stage: Mapped[SalesStage] = mapped_column(
        str_enum(SalesStage, "sales_stage"), default=SalesStage.NEW, index=True
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(default=None)
    next_follow_up_on: Mapped[date | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(Text)

    activities: Mapped[list[ProspectActivity]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan", order_by="ProspectActivity.id"
    )


class ProspectActivity(IdMixin, Base):
    __tablename__ = "prospect_activities"

    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), index=True
    )
    activity_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    prospect: Mapped[Prospect] = relationship(back_populates="activities")


class Client(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("monthly_fee >= 0", name="monthly_fee_nonneg"),
        CheckConstraint("setup_fee >= 0", name="setup_fee_nonneg"),
    )

    prospect_id: Mapped[int | None] = mapped_column(
        ForeignKey("prospects.id", ondelete="SET NULL")
    )
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    website: Mapped[str | None] = mapped_column(String(300))
    industry: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
    contract_status: Mapped[ContractStatus] = mapped_column(
        str_enum(ContractStatus, "contract_status"), default=ContractStatus.PROSPECTIVE, index=True
    )
    start_date: Mapped[date | None] = mapped_column(default=None)
    end_date: Mapped[date | None] = mapped_column(default=None)
    service_package: Mapped[str | None] = mapped_column(String(200))
    monthly_fee: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    setup_fee: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    notes: Mapped[str | None] = mapped_column(Text)


class WebsiteCheck(IdMixin, Base):
    """A fetch of a prospect's public website and the facts extracted from it (Phase 8)."""

    __tablename__ = "prospect_website_checks"

    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[WebsiteCheckStatus] = mapped_column(
        str_enum(WebsiteCheckStatus, "website_check_status"), default=WebsiteCheckStatus.QUEUED, index=True
    )
    url: Mapped[str] = mapped_column(String(500))
    final_url: Mapped[str | None] = mapped_column(String(500))
    http_status: Mapped[int | None] = mapped_column(default=None)
    title: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    pages: Mapped[list[Any] | None] = mapped_column(default=None)
    text_excerpt: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
