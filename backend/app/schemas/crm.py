"""Request schemas for prospects, clients and projects."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ContractStatus, DeliverableStatus, ProjectStatus, ProjectTaskStatus, SalesStage,
)
from app.utils.validation import clean_text, normalize_email, normalize_phone, normalize_website


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _opt(max_len: int):
    return Field(default=None, max_length=max_len)


# -- prospects ---------------------------------------------------------------------------------


class ProspectFields(_In):
    website: str | None = _opt(300)
    industry: str | None = _opt(100)
    address: str | None = _opt(300)
    city: str | None = _opt(100)
    state: str | None = _opt(50)
    zip_code: str | None = _opt(20)
    phone: str | None = _opt(40)
    email: str | None = _opt(255)
    contact_name: str | None = _opt(200)
    contact_title: str | None = _opt(120)
    lead_source: str | None = _opt(100)
    observations: str | None = _opt(5000)
    opportunity: str | None = _opt(5000)
    lead_score: int | None = Field(default=None, ge=0, le=100)
    stage: SalesStage | None = None
    next_follow_up_on: date | None = None
    notes: str | None = _opt(10000)

    @field_validator("website")
    @classmethod
    def _web(cls, v: str | None) -> str | None:
        return normalize_website(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return normalize_email(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("industry", "address", "city", "state", "zip_code", "contact_name",
                     "contact_title", "lead_source")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        return clean_text(v)


class ProspectCreate(ProspectFields):
    company_name: str = Field(min_length=1, max_length=200)
    allow_duplicate: bool = False


class ProspectUpdate(ProspectFields):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)


class ActivityCreate(_In):
    activity_type: Literal["note", "call", "email", "meeting", "follow_up"] = "note"
    description: str = Field(min_length=1, max_length=5000)
    mark_contacted: bool = False


class ConvertProspect(_In):
    service_package: str | None = _opt(200)
    monthly_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    setup_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    start_date: date | None = None


class AgentResearchIn(_In):
    instructions: str = Field(default="Write a prospect research report and a recommended next step "
                                      "based only on the record provided.", min_length=1, max_length=4000)


# -- clients -----------------------------------------------------------------------------------


class ClientFields(_In):
    contact_name: str | None = _opt(200)
    email: str | None = _opt(255)
    phone: str | None = _opt(40)
    website: str | None = _opt(300)
    industry: str | None = _opt(100)
    address: str | None = _opt(300)
    contract_status: ContractStatus | None = None
    start_date: date | None = None
    end_date: date | None = None
    service_package: str | None = _opt(200)
    monthly_fee: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    setup_fee: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    notes: str | None = _opt(10000)

    _web = field_validator("website")(classmethod(lambda cls, v: normalize_website(v)))
    _email = field_validator("email")(classmethod(lambda cls, v: normalize_email(v)))
    _phone = field_validator("phone")(classmethod(lambda cls, v: normalize_phone(v)))


class ClientCreate(ClientFields):
    company_name: str = Field(min_length=1, max_length=200)


class ClientUpdate(ClientFields):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)


# -- projects ----------------------------------------------------------------------------------


class ProjectFields(_In):
    description: str | None = _opt(10000)
    scope: str | None = _opt(10000)
    start_date: date | None = None
    due_date: date | None = None
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    status: ProjectStatus | None = None
    completion_percent: int | None = Field(default=None, ge=0, le=100)
    agent_ids: list[int] | None = Field(default=None, max_length=11)


class ProjectCreate(ProjectFields):
    client_id: int
    name: str = Field(min_length=1, max_length=200)


class ProjectUpdate(ProjectFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scope_change_reason: str | None = Field(default=None, max_length=2000)


class ProjectTaskFields(_In):
    description: str | None = _opt(5000)
    status: ProjectTaskStatus | None = None
    assignee_user_id: int | None = None
    assignee_agent_id: int | None = None
    depends_on_task_id: int | None = None
    due_date: date | None = None


class ProjectTaskCreate(ProjectTaskFields):
    title: str = Field(min_length=1, max_length=300)


class ProjectTaskUpdate(ProjectTaskFields):
    title: str | None = Field(default=None, min_length=1, max_length=300)


class CommentCreate(_In):
    body: str = Field(min_length=1, max_length=5000)
    project_task_id: int | None = None


class DeliverableFields(_In):
    description: str | None = _opt(5000)
    status: DeliverableStatus | None = None
    url: str | None = _opt(500)
    due_date: date | None = None

    _url = field_validator("url")(classmethod(lambda cls, v: normalize_website(v)))


class DeliverableCreate(DeliverableFields):
    title: str = Field(min_length=1, max_length=200)


class DeliverableUpdate(DeliverableFields):
    title: str | None = Field(default=None, min_length=1, max_length=200)
