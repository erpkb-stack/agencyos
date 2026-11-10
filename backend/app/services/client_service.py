"""Clients: CRUD, status tracking and a 360° view (projects, deliverables, invoices, activity)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.crm import Client
from app.models.delivery import Deliverable, Project
from app.models.enums import ACTIVE_PROJECT_STATUSES, ActorType, ContractStatus
from app.models.finance import Invoice, Payment
from app.models.identity import AuditLog, User
from app.services import audit_service
from app.utils.errors import AppError, ConflictError, NotFoundError
from app.utils.time import utcnow

FIELDS = ["company_name", "contact_name", "email", "phone", "website", "industry", "address",
          "contract_status", "start_date", "end_date", "service_package", "monthly_fee", "setup_fee", "notes"]
SORTABLE = {"company_name": Client.company_name, "created_at": Client.created_at,
            "monthly_fee": Client.monthly_fee, "start_date": Client.start_date}


def _money(v: Any) -> str:
    return f"{Decimal(str(v or 0)).quantize(Decimal('0.01'))}"


def serialize(c: Client) -> dict[str, Any]:
    out = {f: getattr(c, f) for f in FIELDS}
    out.update(id=c.id, prospect_id=c.prospect_id, contract_status=c.contract_status.value,
               monthly_fee=_money(c.monthly_fee), setup_fee=_money(c.setup_fee),
               created_at=c.created_at, updated_at=c.updated_at)
    return out


def get(db: Session, client_id: int) -> Client:
    c = db.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Client not found")
    return c


def for_prospect(db: Session, prospect_id: int) -> Client | None:
    return db.scalar(select(Client).where(Client.prospect_id == prospect_id, Client.deleted_at.is_(None)))


def _check_dates(c: Client) -> None:
    if c.start_date and c.end_date and c.end_date < c.start_date:
        raise AppError("End date cannot be before the start date")


def create(db: Session, user: User, data: dict[str, Any]) -> Client:
    dupe = db.scalar(select(Client).where(Client.deleted_at.is_(None),
                                          func.lower(Client.company_name) == data["company_name"].strip().lower()))
    if dupe:
        raise ConflictError("A client with this company name already exists", details={"client_id": dupe.id})
    c = Client(**{k: v for k, v in data.items() if k in FIELDS and v is not None})
    _check_dates(c)
    db.add(c)
    db.flush()
    audit_service.record(db, "client.created", user=user, actor_type=ActorType.USER, entity_type="client",
                         entity_id=c.id, summary=f"Added client {c.company_name}")
    return c


def update(db: Session, user: User, c: Client, changes: dict[str, Any]) -> Client:
    changes = {k: v for k, v in changes.items() if k in FIELDS and not (k == "company_name" and v is None)}
    changed = [k for k, v in changes.items() if getattr(c, k) != v]
    if not changed:
        return c
    old_status = c.contract_status
    for k in changed:
        setattr(c, k, changes[k])
    _check_dates(c)
    summary = f"Updated {c.company_name}: {', '.join(changed)}"
    if "contract_status" in changed:
        summary = f"Contract {old_status.value} → {c.contract_status.value}; " + summary
    audit_service.record(db, "client.updated", user=user, actor_type=ActorType.USER, entity_type="client",
                         entity_id=c.id, summary=summary, details={"fields": changed})
    return c


def soft_delete(db: Session, user: User, c: Client) -> None:
    active = db.scalar(select(func.count()).select_from(Project).where(
        Project.client_id == c.id, Project.deleted_at.is_(None), Project.status.in_(ACTIVE_PROJECT_STATUSES)))
    if active:
        raise ConflictError(f"This client has {active} active project(s); complete or cancel them first")
    c.deleted_at = utcnow()
    audit_service.record(db, "client.deleted", user=user, actor_type=ActorType.USER, entity_type="client",
                         entity_id=c.id, summary=f"Deleted client {c.company_name}")


def search(db: Session, *, q: str | None, contract_status: ContractStatus | None, sort: str, order: str,
           offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    query = select(Client).where(Client.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Client.company_name).like(like), func.lower(Client.contact_name).like(like),
                                func.lower(Client.email).like(like), func.lower(Client.industry).like(like)))
    if contract_status:
        query = query.where(Client.contract_status == contract_status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    col = SORTABLE.get(sort, Client.created_at)
    rows = list(db.scalars(query.order_by(col.asc() if order == "asc" else col.desc(), Client.id.desc())
                           .offset(offset).limit(limit)))
    counts = dict(db.execute(
        select(Project.client_id, func.count()).where(
            Project.client_id.in_([c.id for c in rows]), Project.deleted_at.is_(None),
            Project.status.in_(ACTIVE_PROJECT_STATUSES)).group_by(Project.client_id)
    ).all()) if rows else {}
    return [{**serialize(c), "active_projects": counts.get(c.id, 0)} for c in rows], total


def detail(db: Session, c: Client) -> dict[str, Any]:
    projects = list(db.scalars(select(Project).where(Project.client_id == c.id, Project.deleted_at.is_(None))
                               .order_by(Project.id.desc())))
    project_ids = [p.id for p in projects]
    names = {p.id: p.name for p in projects}
    deliverables = list(db.scalars(select(Deliverable).where(Deliverable.project_id.in_(project_ids))
                                   .order_by(Deliverable.id.desc()))) if project_ids else []
    invoices = list(db.scalars(select(Invoice).where(Invoice.client_id == c.id).order_by(Invoice.id.desc())))
    payments = list(db.scalars(select(Payment).where(Payment.client_id == c.id).order_by(Payment.paid_on.desc())))
    activity = db.execute(
        select(AuditLog).where(or_(
            and_(AuditLog.entity_type == "client", AuditLog.entity_id == c.id),
            and_(AuditLog.entity_type == "project", AuditLog.entity_id.in_(project_ids or [-1])),
        )).order_by(AuditLog.id.desc()).limit(50)
    ).scalars()
    return {
        **serialize(c),
        "projects": [{"id": p.id, "name": p.name, "status": p.status.value, "completion_percent": p.completion_percent,
                      "due_date": p.due_date} for p in projects],
        "deliverables": [{"id": d.id, "title": d.title, "status": d.status.value, "project_id": d.project_id,
                          "project": names.get(d.project_id), "due_date": d.due_date, "url": d.url} for d in deliverables],
        "invoices": [{"id": i.id, "number": i.number, "status": i.status.value, "total": _money(i.total),
                      "issue_date": i.issue_date, "due_date": i.due_date} for i in invoices],
        "payments": [{"id": p.id, "amount": _money(p.amount), "paid_on": p.paid_on, "method": p.method} for p in payments],
        "activity": [{"id": a.id, "action": a.action, "summary": a.summary, "actor": a.actor_label,
                      "created_at": a.created_at} for a in activity],
    }
