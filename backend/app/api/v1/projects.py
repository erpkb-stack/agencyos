from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import ProjectStatus, TaskPriority
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.crm import (
    CommentCreate, DeliverableCreate, DeliverableUpdate, ProjectCreate, ProjectTaskCreate,
    ProjectTaskUpdate, ProjectUpdate,
)
from app.security.deps import get_current_user, require_admin, require_writer
from app.services import project_service, task_service
from app.utils.errors import AppError

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(client_id: int | None = None, status_filter: ProjectStatus | None = Query(None, alias="status"),
                  q: str | None = Query(None, max_length=200), page: int = Query(1, ge=1),
                  page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db),
                  _: User = Depends(get_current_user)) -> dict[str, Any]:
    items, total = project_service.search(db, client_id=client_id, status=status_filter, q=q,
                                          offset=(page - 1) * page_size, limit=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/options")
def options(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    """Choices for project forms: active team members, agents and clients (names only)."""
    from sqlalchemy import select

    from app.models.ai import Agent
    from app.models.crm import Client

    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    agents = db.scalars(select(Agent).where(Agent.is_active.is_(True)).order_by(Agent.id))
    clients = db.scalars(select(Client).where(Client.deleted_at.is_(None)).order_by(Client.company_name))
    return {
        "users": [{"id": u.id, "name": u.full_name} for u in users],
        "agents": [{"id": a.id, "name": a.name} for a in agents],
        "clients": [{"id": c.id, "name": c.company_name} for c in clients],
        "statuses": [s.value for s in ProjectStatus],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.create(db, user, body.model_dump())
    db.commit()
    return project_service.serialize(db, p)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return project_service.detail(db, project_service.get(db, project_id))


@router.patch("/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.update(db, user, project_service.get(db, project_id), body.model_dump(exclude_unset=True))
    db.commit()
    return project_service.serialize(db, p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    project_service.soft_delete(db, user, project_service.get(db, project_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- tasks -------------------------------------------------------------------------------------


@router.post("/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
def create_task(project_id: int, body: ProjectTaskCreate, db: Session = Depends(get_db),
                user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.get(db, project_id)
    t = project_service.create_task(db, user, p, body.model_dump())
    db.commit()
    return project_service.serialize_task(db, t)


@router.patch("/{project_id}/tasks/{task_id}")
def update_task(project_id: int, task_id: int, body: ProjectTaskUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.get(db, project_id)
    t = project_service.update_task(db, user, p, project_service.get_task(db, p, task_id),
                                    body.model_dump(exclude_unset=True))
    db.commit()
    return project_service.serialize_task(db, t)


@router.delete("/{project_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(project_id: int, task_id: int, db: Session = Depends(get_db),
                user: User = Depends(require_writer)) -> Response:
    p = project_service.get(db, project_id)
    project_service.delete_task(db, user, p, project_service.get_task(db, p, task_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/tasks/{task_id}/run-agent", status_code=status.HTTP_202_ACCEPTED)
def run_agent(project_id: int, task_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer),
              runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Hand a project task to its assigned agent. Project data is passed as untrusted context."""
    p = project_service.get(db, project_id)
    t = project_service.get_task(db, p, task_id)
    if t.assignee_agent_id is None:
        raise AppError("Assign an agent to this task first")
    project = project_service.serialize(db, p)
    context = jsonable_encoder({
        "project": {k: project[k] for k in ("name", "client_name", "description", "scope", "status", "due_date", "budget")},
        "project_task": {"title": t.title, "description": t.description, "due_date": t.due_date},
    })
    agent_task = task_service.create_task(
        db, user, agent_id=t.assignee_agent_id, title=f"{p.name}: {t.title}"[:300],
        instructions=f"Complete this project task within the agreed scope: {t.title}"
                     + (f"\n\nDetails: {t.description}" if t.description else ""),
        priority=TaskPriority.NORMAL, context=context, task_type="project_task", project_id=p.id,
    )
    project_service.add_comment(db, user, p, f"Sent to agent (AI task #{agent_task.id})", t.id)
    db.commit()
    runner.submit(agent_task.id)
    return {"task_id": agent_task.id}


# -- comments & deliverables -------------------------------------------------------------------


@router.post("/{project_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(project_id: int, body: CommentCreate, db: Session = Depends(get_db),
                user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.get(db, project_id)
    project_service.add_comment(db, user, p, body.body, body.project_task_id)
    db.commit()
    return {"timeline": project_service.timeline(db, p)}


@router.post("/{project_id}/deliverables", status_code=status.HTTP_201_CREATED)
def add_deliverable(project_id: int, body: DeliverableCreate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.get(db, project_id)
    d = project_service.save_deliverable(db, user, p, body.model_dump(exclude_none=True))
    db.commit()
    return project_service.serialize_deliverable(d)


@router.patch("/{project_id}/deliverables/{deliverable_id}")
def update_deliverable(project_id: int, deliverable_id: int, body: DeliverableUpdate, db: Session = Depends(get_db),
                       user: User = Depends(require_writer)) -> dict[str, Any]:
    p = project_service.get(db, project_id)
    d = project_service.save_deliverable(db, user, p, body.model_dump(exclude_unset=True),
                                         project_service.get_deliverable(db, p, deliverable_id))
    db.commit()
    return project_service.serialize_deliverable(d)
