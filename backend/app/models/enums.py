"""Domain enumerations shared by models, schemas and services."""

from enum import StrEnum


class UserRole(StrEnum):
    FOUNDER = "FOUNDER"
    ADMIN = "ADMIN"
    STAFF = "STAFF"
    READ_ONLY = "READ_ONLY"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SalesStage(StrEnum):
    NEW = "NEW"
    RESEARCHED = "RESEARCHED"
    QUALIFIED = "QUALIFIED"
    CONTACTED = "CONTACTED"
    RESPONDED = "RESPONDED"
    MEETING_SCHEDULED = "MEETING_SCHEDULED"
    PROPOSAL_SENT = "PROPOSAL_SENT"
    NEGOTIATION = "NEGOTIATION"
    WON = "WON"
    LOST = "LOST"


# Pipeline order (LOST is terminal and outside the progression).
SALES_PIPELINE: list[SalesStage] = [s for s in SalesStage if s != SalesStage.LOST]


def stages_from(stage: SalesStage) -> list[SalesStage]:
    """All pipeline stages at or after ``stage`` (excludes LOST)."""
    return SALES_PIPELINE[SALES_PIPELINE.index(stage):]


class ContractStatus(StrEnum):
    PROSPECTIVE = "PROSPECTIVE"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class ProjectStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    AWAITING_CLIENT = "AWAITING_CLIENT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


ACTIVE_PROJECT_STATUSES = [
    ProjectStatus.IN_PROGRESS,
    ProjectStatus.REVIEW,
    ProjectStatus.AWAITING_CLIENT,
]


class ProjectTaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


class DeliverableStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    DELIVERED = "DELIVERED"


class CampaignType(StrEnum):
    COLD_EMAIL = "COLD_EMAIL"
    PROMOTIONAL_EMAIL = "PROMOTIONAL_EMAIL"
    NEWSLETTER = "NEWSLETTER"
    GOOGLE_ADS = "GOOGLE_ADS"
    META_ADS = "META_ADS"
    LEAD_NURTURING = "LEAD_NURTURING"
    REENGAGEMENT = "REENGAGEMENT"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class EmailKind(StrEnum):
    TEMPLATE = "TEMPLATE"
    DRAFT = "DRAFT"
    SEQUENCE_STEP = "SEQUENCE_STEP"


class ContentStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"


class SuppressionReason(StrEnum):
    UNSUBSCRIBED = "UNSUBSCRIBED"
    BOUNCED = "BOUNCED"
    COMPLAINT = "COMPLAINT"
    MANUAL = "MANUAL"


class FunnelStatus(StrEnum):
    DRAFT = "DRAFT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class ApprovalActionType(StrEnum):
    SEND_EMAIL = "SEND_EMAIL"
    LAUNCH_ADS = "LAUNCH_ADS"
    PUBLISH_WEBSITE = "PUBLISH_WEBSITE"
    CHANGE_BUDGET = "CHANGE_BUDGET"
    SPEND_MONEY = "SPEND_MONEY"
    ISSUE_REFUND = "ISSUE_REFUND"
    SIGN_CONTRACT = "SIGN_CONTRACT"
    DELETE_RECORD = "DELETE_RECORD"
    OTHER = "OTHER"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class InvoiceStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    VOID = "VOID"


class RevenueCategory(StrEnum):
    SETUP_FEE = "SETUP_FEE"
    RETAINER = "RETAINER"
    ADDITIONAL_SERVICE = "ADDITIONAL_SERVICE"
    OTHER = "OTHER"


class ExpenseCategory(StrEnum):
    AI_API = "AI_API"
    HOSTING = "HOSTING"
    DOMAINS = "DOMAINS"
    MARKETING_TOOLS = "MARKETING_TOOLS"
    SOFTWARE = "SOFTWARE"
    OTHER = "OTHER"
