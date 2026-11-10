from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TaskPriority

ProviderChoice = Literal["default", "mock", "openai", "anthropic"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CEOMessageIn(_Strict):
    content: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None


class ConversationRename(_Strict):
    title: str = Field(min_length=1, max_length=200)


def _clean_list(items: list[str]) -> list[str]:
    return [i.strip() for i in items if i.strip()]


class AgentCreate(_Strict):
    name: str = Field(min_length=2, max_length=120)
    department: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    system_prompt: str = Field(min_length=20, max_length=20000)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    allowed_tools: list[str] = Field(default_factory=lambda: ["draft_content"], max_length=5)
    model_provider: ProviderChoice = "default"
    model_name: str = Field(default="", max_length=100)
    temperature: float = Field(default=0.3, ge=0, le=1)

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, v: list[str]) -> list[str]:
        return _clean_list(v)


class AgentUpdate(_Strict):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    department: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    system_prompt: str | None = Field(default=None, min_length=20, max_length=20000)
    capabilities: list[str] | None = Field(default=None, max_length=20)
    allowed_tools: list[str] | None = Field(default=None, max_length=5)
    model_provider: ProviderChoice | None = None
    model_name: str | None = Field(default=None, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=1)
    is_active: bool | None = None


class AgentTestIn(_Strict):
    instructions: str = Field(min_length=1, max_length=8000)


class TaskCreate(_Strict):
    agent_id: int
    title: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=8000)
    priority: TaskPriority = TaskPriority.NORMAL
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionIn(_Strict):
    decision: Literal["APPROVED", "REJECTED"]
    comments: str | None = Field(default=None, max_length=2000)
