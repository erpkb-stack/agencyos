"""Import every model so ``Base.metadata`` is complete (Alembic, create_all)."""

from app.models.ai import Agent, AgentExecution, AgentTask, Conversation, Message
from app.models.approvals import Approval
from app.models.crm import Client, Prospect, ProspectActivity, WebsiteCheck
from app.models.delivery import Deliverable, Project, ProjectComment, ProjectTask, project_agents
from app.models.finance import Expense, Invoice, InvoiceItem, Payment, Revenue
from app.models.identity import AuditLog, User, UserSession
from app.models.marketing import Campaign, EmailSuppression, EmailTemplate, Funnel

__all__ = [
    "Agent",
    "AgentExecution",
    "AgentTask",
    "Approval",
    "AuditLog",
    "Campaign",
    "Client",
    "Conversation",
    "Deliverable",
    "EmailSuppression",
    "EmailTemplate",
    "Expense",
    "Funnel",
    "Invoice",
    "InvoiceItem",
    "Message",
    "Payment",
    "Project",
    "ProjectComment",
    "ProjectTask",
    "Prospect",
    "ProspectActivity",
    "Revenue",
    "User",
    "UserSession",
    "WebsiteCheck",
    "project_agents",
]
