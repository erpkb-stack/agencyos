"""Structured input/output models shared by all agents.

Output models avoid free-form objects so they convert to strict JSON schemas for both vendors.
Value limits are enforced by the validators here (strict schemas drop them).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ApprovalActionType, TaskPriority

_MAX_TEXT = 20_000


def _clip(value: str, limit: int = _MAX_TEXT) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


class AgentInput(BaseModel):
    """What an agent receives. ``context`` is untrusted data and is never treated as instructions."""

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(min_length=1, max_length=8000)
    context: dict[str, Any] = Field(default_factory=dict)


class Deliverable(BaseModel):
    title: str
    kind: str = Field(description="e.g. report, plan, email_draft, ad_copy, page_outline, checklist")
    content: str

    @field_validator("title", "kind")
    @classmethod
    def _short(cls, v: str) -> str:
        return _clip(v, 300)

    @field_validator("content")
    @classmethod
    def _long(cls, v: str) -> str:
        return _clip(v)


class ProposedAction(BaseModel):
    """An external action the agent wants to take. It only ever becomes an approval request."""

    action_type: ApprovalActionType
    title: str
    details: str

    @field_validator("title")
    @classmethod
    def _t(cls, v: str) -> str:
        return _clip(v, 300)

    @field_validator("details")
    @classmethod
    def _d(cls, v: str) -> str:
        return _clip(v, 4000)


class SpecialistOutput(BaseModel):
    summary: str
    deliverables: list[Deliverable]
    recommendations: list[str]
    proposed_actions: list[ProposedAction]
    assumptions: list[str]
    open_questions: list[str]

    @field_validator("summary")
    @classmethod
    def _s(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return _clip(v, 4000)

    @field_validator("recommendations", "assumptions", "open_questions")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return [_clip(i, 1000) for i in v if i.strip()][:20]


class PlannedSubtask(BaseModel):
    agent_key: str
    title: str
    instructions: str
    priority: TaskPriority

    @field_validator("title")
    @classmethod
    def _t(cls, v: str) -> str:
        return _clip(v, 300)

    @field_validator("instructions")
    @classmethod
    def _i(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instructions must not be empty")
        return _clip(v, 8000)


class CEOPlan(BaseModel):
    understanding: str = Field(description="One or two sentences restating the request")
    task_type: str = Field(description="Short snake_case classification, e.g. lead_generation")
    reply: str = Field(description="Message to the founder, used directly if no delegation is needed")
    subtasks: list[PlannedSubtask]

    @field_validator("task_type")
    @classmethod
    def _tt(cls, v: str) -> str:
        cleaned = "".join(c if c.isalnum() else "_" for c in v.strip().lower()).strip("_")
        return (cleaned or "general")[:60]


class CEOSynthesis(BaseModel):
    summary: str
    next_steps: list[str]

    @field_validator("summary")
    @classmethod
    def _s(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary must not be empty")
        return _clip(v, 8000)
