"""Request schemas for campaigns, email content and funnels."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CampaignType, EmailKind, SalesStage, SuppressionReason
from app.utils.validation import normalize_email, normalize_website


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# -- campaigns ---------------------------------------------------------------------------------


class CampaignFields(_In):
    client_id: int | None = None
    target_audience: str | None = Field(default=None, max_length=5000)
    objectives: str | None = Field(default=None, max_length=5000)
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    start_date: date | None = None
    end_date: date | None = None


class CampaignCreate(CampaignFields):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: CampaignType


class CampaignUpdate(CampaignFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CampaignStatusIn(_In):
    status: Literal["ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"]
    note: str | None = Field(default=None, max_length=1000)


class GenerateIn(_In):
    kind: Literal["strategy", "emails", "ads", "audience"]
    notes: str | None = Field(default=None, max_length=2000)


class AudienceFilter(_In):
    stages: list[SalesStage] = Field(default_factory=list, max_length=10)
    industry: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    min_score: int | None = Field(default=None, ge=0, le=100)


ConsentBasis = Literal["existing_customers", "opted_in", "b2b_cold_outreach"]


class CampaignApprovalIn(_In):
    consent_basis: ConsentBasis | None = None
    confirm_compliance: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class MetricsIn(_In):
    sent: int | None = Field(default=None, ge=0)
    delivered: int | None = Field(default=None, ge=0)
    opens: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    leads: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    spend: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


# -- email -------------------------------------------------------------------------------------


class EmailFields(_In):
    campaign_id: int | None = None
    client_id: int | None = None
    kind: EmailKind | None = None
    sequence_order: int | None = Field(default=None, ge=1, le=50)
    delay_days: int | None = Field(default=None, ge=0, le=365)


class EmailCreate(EmailFields):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body_text: str = Field(min_length=1, max_length=20000)


class EmailUpdate(EmailFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body_text: str | None = Field(default=None, min_length=1, max_length=20000)


class EmailCheckIn(_In):
    subject: str = Field(default="", max_length=300)
    body_text: str = Field(default="", max_length=20000)


class EmailGenerateIn(_In):
    purpose: Literal["sequence", "subject_lines", "single"]
    brief: str = Field(min_length=1, max_length=4000)
    campaign_id: int | None = None


class SuppressionIn(_In):
    email: str = Field(min_length=3, max_length=255)
    reason: SuppressionReason = SuppressionReason.MANUAL
    source: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)  # type: ignore[return-value]


class RecipientsCheckIn(_In):
    emails: list[str] = Field(max_length=5000)


# -- funnels -----------------------------------------------------------------------------------


class LeadField(_In):
    name: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    type: Literal["text", "email", "tel", "select", "textarea", "number"] = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=30)


class FunnelCopy(_In):
    headline: str | None = Field(default=None, max_length=200)
    subheadline: str | None = Field(default=None, max_length=400)
    benefits: list[str] = Field(default_factory=list, max_length=8)
    proof: str | None = Field(default=None, max_length=400)
    business_name: str | None = Field(default=None, max_length=120)
    phone_display: str | None = Field(default=None, max_length=40)


class FormConfig(_In):
    submit_label: str | None = Field(default=None, max_length=60)
    success_message: str | None = Field(default=None, max_length=300)
    consent_text: str | None = Field(default=None, max_length=600)
    action_url: str | None = Field(default=None, max_length=500)
    copy_: FunnelCopy = Field(default_factory=FunnelCopy, alias="copy")

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    @field_validator("action_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return normalize_website(v)


class FollowUpStep(_In):
    delay_minutes: int = Field(ge=0, le=60 * 24 * 60)
    action: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=500)


class FunnelFields(_In):
    client_id: int | None = None
    industry: str | None = Field(default=None, max_length=100)
    landing_page_url: str | None = Field(default=None, max_length=500)
    target_conversion: str | None = Field(default=None, max_length=200)
    call_to_action: str | None = Field(default=None, max_length=200)
    lead_fields: list[LeadField] | None = Field(default=None, max_length=15)
    form_config: FormConfig | None = None
    follow_up_steps: list[FollowUpStep] | None = Field(default=None, max_length=15)

    @field_validator("landing_page_url")
    @classmethod
    def _lp(cls, v: str | None) -> str | None:
        return normalize_website(v)


class FunnelCreate(FunnelFields):
    name: str = Field(min_length=1, max_length=200)
    template_key: str | None = Field(default=None, max_length=50)


class FunnelUpdate(FunnelFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class FunnelPublishedIn(_In):
    landing_page_url: str = Field(min_length=4, max_length=500)

    @field_validator("landing_page_url")
    @classmethod
    def _lp(cls, v: str) -> str:
        return normalize_website(v)  # type: ignore[return-value]

