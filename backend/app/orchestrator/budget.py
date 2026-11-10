"""AI spending limits, enforced in Python before every model call.

* Per request: the estimated cost of a CEO request (the root task plus its sub-tasks), or of a
  single direct task, may not exceed ``AI_COST_LIMIT_PER_REQUEST`` (0 = no limit).
* Per month: total estimated AI cost this calendar month may not exceed
  ``AI_COST_LIMIT_PER_MONTH`` (0 = no limit). New work is refused once the cap is reached.

Costs are estimates from list prices (``providers/pricing.py``). Calls to models without a known
price count as $0, and calls already in flight when a limit is reached still finish, so a limit can
be overshot by at most one call per running agent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents.base import AgentError
from app.config.settings import Settings
from app.models.ai import AgentExecution, AgentTask
from app.services.finance_service import day_start
from app.utils.errors import ConflictError

ZERO = Decimal("0")


class BudgetExceeded(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def _money(value) -> Decimal:
    return Decimal(str(value or 0))


def request_spend(db: Session, root_id: int) -> Decimal:
    task_ids = select(AgentTask.id).where(or_(AgentTask.id == root_id, AgentTask.parent_task_id == root_id))
    return _money(db.scalar(select(func.sum(AgentExecution.estimated_cost))
                            .where(AgentExecution.task_id.in_(task_ids))))


def month_spend(db: Session, today: date | None = None) -> Decimal:
    today = today or date.today()
    start = day_start(today.replace(day=1))
    return _money(db.scalar(select(func.sum(AgentExecution.estimated_cost))
                            .where(AgentExecution.started_at >= start)))


def root_id_of(task: AgentTask) -> int:
    return task.parent_task_id or task.id


def check_before_call(db: Session, settings: Settings, task: AgentTask) -> None:
    """Raise BudgetExceeded if another model call for ``task`` would break a limit."""
    per_request = _money(settings.ai_cost_limit_per_request)
    if per_request > 0:
        spent = request_spend(db, root_id_of(task))
        if spent >= per_request:
            raise BudgetExceeded(
                f"AI cost limit for this request reached (${spent:.4f} of ${per_request:.2f}); "
                "raise AI_COST_LIMIT_PER_REQUEST to allow more")
    check_month(db, settings, as_agent_error=True)


def check_month(db: Session, settings: Settings, *, as_agent_error: bool = False) -> None:
    per_month = _money(settings.ai_cost_limit_per_month)
    if per_month <= 0:
        return
    spent = month_spend(db)
    if spent >= per_month:
        message = (f"Monthly AI cost limit reached (${spent:.2f} of ${per_month:.2f}); "
                   "raise AI_COST_LIMIT_PER_MONTH or wait until next month")
        if as_agent_error:
            raise BudgetExceeded(message)
        raise ConflictError(message)


def summary(db: Session, settings: Settings) -> dict:
    return {
        "per_request_limit": f"{_money(settings.ai_cost_limit_per_request):.2f}",
        "per_month_limit": f"{_money(settings.ai_cost_limit_per_month):.2f}",
        "month_spend": f"{month_spend(db):.4f}",
    }
