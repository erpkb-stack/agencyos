"""Founder dashboard aggregates. Every number is a database query; nothing is hardcoded.

Metric definitions are documented in docs/DATABASE_DESIGN.md.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    AgentExecution,
    AgentTask,
    Approval,
    AuditLog,
    Client,
    Project,
    Prospect,
    Revenue,
)
from app.models.enums import (
    ACTIVE_PROJECT_STATUSES,
    SALES_PIPELINE,
    ApprovalStatus,
    ContractStatus,
    ExecutionStatus,
    ProjectStatus,
    SalesStage,
    TaskStatus,
    stages_from,
)
from app.utils.time import ensure_aware

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _month_keys(today: date, months: int) -> list[str]:
    keys: list[str] = []
    year, month = today.year, today.month
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def _prospect_stage_counts(db: Session) -> dict[SalesStage, int]:
    rows = db.execute(
        select(Prospect.stage, func.count())
        .where(Prospect.deleted_at.is_(None))
        .group_by(Prospect.stage)
    ).all()
    counts = {stage: 0 for stage in SalesStage}
    for stage, count in rows:
        counts[SalesStage(stage)] = count
    return counts


def kpis(db: Session) -> dict[str, Any]:
    stage_counts = _prospect_stage_counts(db)

    def at_or_after(stage: SalesStage) -> int:
        return sum(stage_counts[s] for s in stages_from(stage))

    client_row = db.execute(
        select(
            func.count(),
            func.sum(case((Client.contract_status == ContractStatus.ACTIVE, 1), else_=0)),
            func.sum(
                case(
                    (Client.contract_status == ContractStatus.ACTIVE, Client.monthly_fee),
                    else_=0,
                )
            ),
        ).where(Client.deleted_at.is_(None))
    ).one()

    return {
        "total_revenue": str(money(db.scalar(select(func.sum(Revenue.amount))))),
        "monthly_recurring_revenue": str(money(client_row[2])),
        "total_clients": client_row[0] or 0,
        "active_clients": int(client_row[1] or 0),
        "total_prospects": sum(stage_counts.values()),
        "qualified_leads": at_or_after(SalesStage.QUALIFIED),
        "discovery_meetings": at_or_after(SalesStage.MEETING_SCHEDULED),
        "proposals_sent": at_or_after(SalesStage.PROPOSAL_SENT),
        "closed_deals": stage_counts[SalesStage.WON],
        "active_projects": db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.deleted_at.is_(None), Project.status.in_(ACTIVE_PROJECT_STATUSES))
        )
        or 0,
        "pending_approvals": db.scalar(
            select(func.count())
            .select_from(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
        )
        or 0,
    }


def monthly_revenue(db: Session, today: date, months: int = 12) -> dict[str, Any]:
    keys = _month_keys(today, months)
    start = date(int(keys[0][:4]), int(keys[0][5:]), 1)
    totals = {k: Decimal("0") for k in keys}
    for received_on, amount in db.execute(
        select(Revenue.received_on, Revenue.amount).where(Revenue.received_on >= start)
    ):
        key = f"{received_on.year:04d}-{received_on.month:02d}"
        if key in totals:
            totals[key] += Decimal(str(amount))
    return {"labels": keys, "values": [str(money(totals[k])) for k in keys]}


def pipeline(db: Session) -> dict[str, Any]:
    counts = _prospect_stage_counts(db)
    stages = [*SALES_PIPELINE, SalesStage.LOST]
    return {"labels": [s.value for s in stages], "values": [counts[s] for s in stages]}


def lead_conversion(db: Session) -> dict[str, Any]:
    """Cumulative funnel: how many prospects reached each milestone."""
    counts = _prospect_stage_counts(db)
    total = sum(counts.values())
    milestones = [
        ("Prospects", None),
        ("Qualified", SalesStage.QUALIFIED),
        ("Contacted", SalesStage.CONTACTED),
        ("Meeting", SalesStage.MEETING_SCHEDULED),
        ("Proposal", SalesStage.PROPOSAL_SENT),
        ("Won", SalesStage.WON),
    ]
    values = [
        total if stage is None else sum(counts[s] for s in stages_from(stage))
        for _, stage in milestones
    ]
    return {"labels": [m[0] for m in milestones], "values": values}


def project_completion(db: Session) -> dict[str, Any]:
    rows = dict(
        db.execute(
            select(Project.status, func.count())
            .where(Project.deleted_at.is_(None))
            .group_by(Project.status)
        ).all()
    )
    statuses = list(ProjectStatus)
    avg = db.scalar(
        select(func.avg(Project.completion_percent)).where(
            Project.deleted_at.is_(None), Project.status != ProjectStatus.CANCELLED
        )
    )
    return {
        "labels": [s.value for s in statuses],
        "values": [rows.get(s, 0) for s in statuses],
        "average_completion": round(float(avg), 1) if avg is not None else None,
    }


def agent_performance(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(
            Agent.name,
            func.sum(case((AgentExecution.status == ExecutionStatus.SUCCEEDED, 1), else_=0)),
            func.sum(case((AgentExecution.status == ExecutionStatus.FAILED, 1), else_=0)),
        )
        .join(AgentExecution, AgentExecution.agent_id == Agent.id)
        .group_by(Agent.id, Agent.name)
        .order_by(Agent.name)
    ).all()
    return {
        "labels": [r[0] for r in rows],
        "succeeded": [int(r[1] or 0) for r in rows],
        "failed": [int(r[2] or 0) for r in rows],
    }


def _iso(value: Any) -> str | None:
    value = ensure_aware(value)
    return value.isoformat() if value else None


def activity(db: Session, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    audit = db.scalars(
        select(AuditLog)
        .where(~AuditLog.action.like("auth.%"))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    executions = db.execute(
        select(AgentExecution, Agent.name)
        .join(Agent, Agent.id == AgentExecution.agent_id)
        .order_by(AgentExecution.started_at.desc())
        .limit(limit)
    ).all()
    prospects = db.scalars(
        select(Prospect)
        .where(Prospect.deleted_at.is_(None))
        .order_by(Prospect.created_at.desc())
        .limit(limit)
    ).all()
    clients = db.scalars(
        select(Client)
        .where(Client.deleted_at.is_(None))
        .order_by(Client.created_at.desc())
        .limit(limit)
    ).all()
    approvals = db.scalars(
        select(Approval)
        .where(Approval.status == ApprovalStatus.PENDING)
        .order_by(Approval.created_at.desc())
        .limit(limit)
    ).all()
    failed = db.scalars(
        select(AgentTask)
        .where(AgentTask.status == TaskStatus.FAILED)
        .order_by(AgentTask.updated_at.desc())
        .limit(limit)
    ).all()
    return {
        "ceo_actions": [
            {
                "id": a.id,
                "action": a.action,
                "summary": a.summary or a.action,
                "actor": a.actor_label,
                "at": _iso(a.created_at),
            }
            for a in audit
        ],
        "agent_executions": [
            {
                "id": e.id,
                "agent": name,
                "status": e.status.value,
                "task_id": e.task_id,
                "at": _iso(e.started_at),
            }
            for e, name in executions
        ],
        "new_prospects": [
            {
                "id": p.id,
                "company_name": p.company_name,
                "city": p.city,
                "industry": p.industry,
                "stage": p.stage.value,
                "at": _iso(p.created_at),
            }
            for p in prospects
        ],
        "new_clients": [
            {
                "id": c.id,
                "company_name": c.company_name,
                "contract_status": c.contract_status.value,
                "at": _iso(c.created_at),
            }
            for c in clients
        ],
        "pending_approvals": [
            {
                "id": a.id,
                "title": a.title,
                "action_type": a.action_type.value,
                "at": _iso(a.created_at),
            }
            for a in approvals
        ],
        "failed_tasks": [
            {"id": t.id, "title": t.title, "error": t.error, "at": _iso(t.updated_at)}
            for t in failed
        ],
    }


def summary(db: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    k = kpis(db)
    has_data = any(
        [
            k["total_prospects"],
            k["total_clients"],
            money(k["total_revenue"]) > 0,
            k["pending_approvals"],
            db.scalar(select(func.count()).select_from(AgentTask)),
            db.scalar(select(func.count()).select_from(Project).where(Project.deleted_at.is_(None))),
        ]
    )
    return {
        "kpis": k,
        "charts": {
            "monthly_revenue": monthly_revenue(db, today),
            "pipeline": pipeline(db),
            "lead_conversion": lead_conversion(db),
            "project_completion": project_completion(db),
            "agent_performance": agent_performance(db),
        },
        "activity": activity(db),
        "is_empty": not has_data,
    }
