"""Projects: lifecycle, tasks with dependencies, comments, deliverables, timeline and progress.

Rules enforced here:
* Scope changes need a stated reason and are written to the timeline (no silent scope creep).
* A task cannot start or finish while the task it depends on is not done; dependency cycles are refused.
* Once a project has tasks, completion is calculated from them (done / total).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.ai import Agent
from app.models.crm import Client
from app.models.delivery import Deliverable, Project, ProjectComment, ProjectTask
from app.models.enums import ActorType, DeliverableStatus, ProjectStatus, ProjectTaskStatus as TS
from app.models.identity import AuditLog, User
from app.services import audit_service
from app.utils.errors import AppError, NotFoundError
from app.utils.time import utcnow

FIELDS = ["name", "description", "scope", "start_date", "due_date", "budget", "status", "completion_percent"]


def _money(v: Any) -> str | None:
    return None if v is None else f"{Decimal(str(v)).quantize(Decimal('0.01'))}"


def get(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None or p.deleted_at is not None:
        raise NotFoundError("Project not found")
    return p


def _client(db: Session, client_id: int) -> Client:
    c = db.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Client not found")
    return c


def _agents(db: Session, ids: list[int]) -> list[Agent]:
    agents = list(db.scalars(select(Agent).where(Agent.id.in_(ids)))) if ids else []
    if len(agents) != len(set(ids)):
        raise AppError("One or more agents do not exist")
    return agents


def _audit(db: Session, user: User, p: Project, action: str, summary: str, details: dict | None = None) -> None:
    audit_service.record(db, action, user=user, actor_type=ActorType.USER, entity_type="project",
                         entity_id=p.id, summary=summary, details=details)


def _check_dates(start, due) -> None:
    if start and due and due < start:
        raise AppError("Due date cannot be before the start date")


def task_counts(db: Session, project_ids: list[int]) -> dict[int, tuple[int, int]]:
    if not project_ids:
        return {}
    rows = db.execute(
        select(ProjectTask.project_id, func.count(), func.sum(case((ProjectTask.status == TS.DONE, 1), else_=0)))
        .where(ProjectTask.project_id.in_(project_ids)).group_by(ProjectTask.project_id)
    ).all()
    return {pid: (total, int(done or 0)) for pid, total, done in rows}


def recompute_completion(db: Session, p: Project) -> None:
    db.flush()
    total, done = task_counts(db, [p.id]).get(p.id, (0, 0))
    if total:
        p.completion_percent = round(done * 100 / total)


def serialize(db: Session, p: Project, counts: dict[int, tuple[int, int]] | None = None) -> dict[str, Any]:
    client = db.get(Client, p.client_id)
    total, done = (counts or task_counts(db, [p.id])).get(p.id, (0, 0))
    return {
        "id": p.id, "client_id": p.client_id, "client_name": client.company_name if client else None,
        "name": p.name, "description": p.description, "scope": p.scope, "start_date": p.start_date,
        "due_date": p.due_date, "budget": _money(p.budget), "status": p.status.value,
        "completion_percent": p.completion_percent, "completion_from_tasks": total > 0,
        "tasks_total": total, "tasks_done": done,
        "agents": [{"id": a.id, "name": a.name} for a in p.agents],
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


# -- projects ----------------------------------------------------------------------------------


def create(db: Session, user: User, data: dict[str, Any]) -> Project:
    client = _client(db, data["client_id"])
    _check_dates(data.get("start_date"), data.get("due_date"))
    p = Project(client_id=client.id, **{k: v for k, v in data.items() if k in FIELDS and v is not None})
    p.agents = _agents(db, data.get("agent_ids") or [])
    db.add(p)
    db.flush()
    _audit(db, user, p, "project.created", f"Created project {p.name} for {client.company_name}")
    return p


def update(db: Session, user: User, p: Project, changes: dict[str, Any]) -> Project:
    reason = (changes.pop("scope_change_reason", None) or "").strip()
    agent_ids = changes.pop("agent_ids", None)
    changes = {k: v for k, v in changes.items() if k in FIELDS and not (k == "name" and v is None)}
    if "scope" in changes and (changes["scope"] or None) != (p.scope or None) and p.scope:
        if not reason:
            raise AppError("Changing the scope needs a reason, for example the client's change request")
    if "completion_percent" in changes and task_counts(db, [p.id]).get(p.id, (0, 0))[0]:
        if changes["completion_percent"] != p.completion_percent:
            raise AppError("Completion is calculated from the project's tasks; update the tasks instead")
        changes.pop("completion_percent")
    changed = [k for k, v in changes.items() if getattr(p, k) != v]
    old = {k: getattr(p, k) for k in changed}
    for k in changed:
        setattr(p, k, changes[k])
    _check_dates(p.start_date, p.due_date)
    if p.status == ProjectStatus.COMPLETED and "status" in changed and not task_counts(db, [p.id]).get(p.id):
        p.completion_percent = 100
    if agent_ids is not None:
        new_agents = _agents(db, agent_ids)
        if {a.id for a in new_agents} != {a.id for a in p.agents}:
            p.agents = new_agents
            changed.append("agents")
    if not changed:
        return p
    if "scope" in changed:
        db.add(ProjectComment(project_id=p.id, user_id=user.id,
                              body=f"Scope changed. Reason: {reason or 'initial scope set'}\n\nNew scope:\n{p.scope or '(empty)'}"))
    summary = f"Updated {p.name}: {', '.join(changed)}"
    if "status" in changed:
        summary = f"Status {old['status'].value} → {p.status.value}; " + summary
    _audit(db, user, p, "project.updated", summary, {"fields": changed, **({"scope_reason": reason} if reason else {})})
    return p


def soft_delete(db: Session, user: User, p: Project) -> None:
    p.deleted_at = utcnow()
    _audit(db, user, p, "project.deleted", f"Deleted project {p.name}")


def search(db: Session, *, client_id: int | None, status: ProjectStatus | None, q: str | None,
           offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    query = select(Project).where(Project.deleted_at.is_(None))
    if client_id:
        query = query.where(Project.client_id == client_id)
    if status:
        query = query.where(Project.status == status)
    if q:
        query = query.where(func.lower(Project.name).like(f"%{q.strip().lower()}%"))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Project.id.desc()).offset(offset).limit(limit)))
    counts = task_counts(db, [p.id for p in rows])
    return [serialize(db, p, counts) for p in rows], total


# -- tasks -------------------------------------------------------------------------------------


def get_task(db: Session, p: Project, task_id: int) -> ProjectTask:
    t = db.get(ProjectTask, task_id)
    if t is None or t.project_id != p.id:
        raise NotFoundError("Project task not found")
    return t


def _validate_task(db: Session, p: Project, t: ProjectTask) -> None:
    if t.assignee_user_id is not None:
        u = db.get(User, t.assignee_user_id)
        if u is None or not u.is_active:
            raise AppError("Assignee must be an active user")
    if t.assignee_agent_id is not None and db.get(Agent, t.assignee_agent_id) is None:
        raise AppError("Assigned agent does not exist")
    if t.depends_on_task_id is not None:
        seen = {t.id} if t.id else set()
        current = get_task(db, p, t.depends_on_task_id)  # same project only
        while current is not None:
            if current.id in seen:
                raise AppError("This dependency would create a cycle")
            seen.add(current.id)
            current = db.get(ProjectTask, current.depends_on_task_id) if current.depends_on_task_id else None
        blocker = db.get(ProjectTask, t.depends_on_task_id)
        if t.status in (TS.IN_PROGRESS, TS.DONE) and blocker.status != TS.DONE:
            raise AppError(f"Blocked: '{blocker.title}' must be done first")


def serialize_task(db: Session, t: ProjectTask) -> dict[str, Any]:
    user = db.get(User, t.assignee_user_id) if t.assignee_user_id else None
    agent = db.get(Agent, t.assignee_agent_id) if t.assignee_agent_id else None
    blocker = db.get(ProjectTask, t.depends_on_task_id) if t.depends_on_task_id else None
    return {
        "id": t.id, "project_id": t.project_id, "title": t.title, "description": t.description,
        "status": t.status.value, "due_date": t.due_date, "completed_at": t.completed_at,
        "assignee_user": {"id": user.id, "name": user.full_name} if user else None,
        "assignee_agent": {"id": agent.id, "name": agent.name} if agent else None,
        "depends_on": {"id": blocker.id, "title": blocker.title, "status": blocker.status.value} if blocker else None,
        "blocked": bool(blocker and blocker.status != TS.DONE),
        "created_at": t.created_at, "updated_at": t.updated_at,
    }


def create_task(db: Session, user: User, p: Project, data: dict[str, Any]) -> ProjectTask:
    t = ProjectTask(project_id=p.id, **{k: v for k, v in data.items() if v is not None})
    t.status = t.status or TS.TODO
    _validate_task(db, p, t)
    if t.status == TS.DONE:
        t.completed_at = utcnow()
    db.add(t)
    recompute_completion(db, p)
    _audit(db, user, p, "project.task_created", f"Task added: {t.title}")
    return t


def update_task(db: Session, user: User, p: Project, t: ProjectTask, changes: dict[str, Any]) -> ProjectTask:
    changes = {k: v for k, v in changes.items() if not (k == "title" and v is None)}
    if changes.get("depends_on_task_id") == t.id:
        raise AppError("A task cannot depend on itself")
    old_status = t.status
    for k, v in changes.items():
        setattr(t, k, v)
    _validate_task(db, p, t)
    if t.status == TS.DONE and old_status != TS.DONE:
        t.completed_at = utcnow()
    elif t.status != TS.DONE:
        t.completed_at = None
    recompute_completion(db, p)
    if t.status != old_status:
        _audit(db, user, p, "project.task_status", f"Task '{t.title}': {old_status.value} → {t.status.value}")
    return t


def delete_task(db: Session, user: User, p: Project, t: ProjectTask) -> None:
    for dependant in db.scalars(select(ProjectTask).where(ProjectTask.depends_on_task_id == t.id)):
        dependant.depends_on_task_id = None
    db.delete(t)
    recompute_completion(db, p)
    _audit(db, user, p, "project.task_deleted", f"Task removed: {t.title}")


# -- comments, deliverables, timeline ----------------------------------------------------------


def add_comment(db: Session, user: User, p: Project, body: str, task_id: int | None) -> ProjectComment:
    if task_id is not None:
        get_task(db, p, task_id)
    c = ProjectComment(project_id=p.id, project_task_id=task_id, user_id=user.id, body=body)
    db.add(c)
    return c


def get_deliverable(db: Session, p: Project, deliverable_id: int) -> Deliverable:
    d = db.get(Deliverable, deliverable_id)
    if d is None or d.project_id != p.id:
        raise NotFoundError("Deliverable not found")
    return d


def serialize_deliverable(d: Deliverable) -> dict[str, Any]:
    return {"id": d.id, "project_id": d.project_id, "title": d.title, "description": d.description,
            "status": d.status.value, "url": d.url, "due_date": d.due_date, "delivered_at": d.delivered_at,
            "created_at": d.created_at}


def save_deliverable(db: Session, user: User, p: Project, data: dict[str, Any], d: Deliverable | None = None) -> Deliverable:
    creating = d is None
    d = d or Deliverable(project_id=p.id)
    old = None if creating else d.status
    for k, v in data.items():
        if not (k == "title" and v is None):
            setattr(d, k, v)
    d.status = d.status or DeliverableStatus.DRAFT
    if d.status == DeliverableStatus.DELIVERED and old != DeliverableStatus.DELIVERED:
        d.delivered_at = utcnow()
    elif d.status != DeliverableStatus.DELIVERED:
        d.delivered_at = None
    db.add(d)
    db.flush()
    if creating:
        _audit(db, user, p, "project.deliverable_added", f"Deliverable added: {d.title}")
    elif old != d.status:
        _audit(db, user, p, "project.deliverable_status", f"Deliverable '{d.title}': {old.value} → {d.status.value}")
    return d


def timeline(db: Session, p: Project) -> list[dict[str, Any]]:
    events = [
        {"kind": "event", "text": a.summary, "actor": a.actor_label, "at": a.created_at}
        for a in db.scalars(select(AuditLog).where(and_(AuditLog.entity_type == "project", AuditLog.entity_id == p.id))
                            .order_by(AuditLog.id.desc()).limit(200))
    ]
    names = {u.id: u.full_name for u in db.scalars(select(User))}
    events += [
        {"kind": "comment", "text": c.body, "actor": names.get(c.user_id), "at": c.created_at,
         "task_id": c.project_task_id}
        for c in db.scalars(select(ProjectComment).where(ProjectComment.project_id == p.id))
    ]
    return sorted(events, key=lambda e: e["at"], reverse=True)


def detail(db: Session, p: Project) -> dict[str, Any]:
    tasks = list(db.scalars(select(ProjectTask).where(ProjectTask.project_id == p.id).order_by(ProjectTask.id)))
    deliverables = list(db.scalars(select(Deliverable).where(Deliverable.project_id == p.id).order_by(Deliverable.id)))
    from app.models.ai import AgentTask
    from app.services.task_service import task_summary

    agent_tasks = list(db.scalars(select(AgentTask).where(AgentTask.project_id == p.id).order_by(AgentTask.id.desc()).limit(20)))
    return {
        **serialize(db, p),
        "tasks": [serialize_task(db, t) for t in tasks],
        "deliverables": [serialize_deliverable(d) for d in deliverables],
        "timeline": timeline(db, p),
        "agent_tasks": [task_summary(t) for t in agent_tasks],
    }


def active_projects_snapshot(db: Session, limit: int = 25) -> list[dict[str, Any]]:
    """Read-only summary for agents with the business-data permission."""
    from app.models.enums import ACTIVE_PROJECT_STATUSES

    rows = list(db.scalars(select(Project).where(Project.deleted_at.is_(None), or_(
        Project.status.in_(ACTIVE_PROJECT_STATUSES), Project.status == ProjectStatus.PLANNED))
        .order_by(Project.due_date.is_(None), Project.due_date).limit(limit)))
    counts = task_counts(db, [p.id for p in rows])
    out = []
    for p in rows:
        client = db.get(Client, p.client_id)
        total, done = counts.get(p.id, (0, 0))
        out.append({"project": p.name, "client": client.company_name if client else None, "status": p.status.value,
                    "completion_percent": p.completion_percent, "due_date": p.due_date.isoformat() if p.due_date else None,
                    "tasks_done": done, "tasks_total": total})
    return out
