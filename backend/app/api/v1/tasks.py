from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import TaskStatus
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.ai import TaskCreate
from app.security.deps import get_current_user, require_writer
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
def list_tasks(
    status_filter: TaskStatus | None = Query(None, alias="status"),
    agent_id: int | None = None,
    root_only: bool = False,
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows, total = task_service.list_tasks(
        db, status=status_filter, agent_id=agent_id, root_only=root_only, q=q,
        offset=(page - 1) * page_size, limit=page_size,
    )
    return {"items": [task_service.task_summary(t) for t in rows], "total": total,
            "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_task(body: TaskCreate, db: Session = Depends(get_db), user: User = Depends(require_writer),
                runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    task = task_service.create_task(
        db, user, agent_id=body.agent_id, title=body.title, instructions=body.instructions,
        priority=body.priority, context=body.context,
    )
    runner.submit(task.id)
    return task_service.task_summary(task)


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return task_service.task_detail(db, task_service.get_task(db, task_id))


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    task = task_service.cancel_task(db, user, task_service.get_task(db, task_id))
    return task_service.task_summary(task)


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer),
               runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    task = task_service.retry_task(db, user, task_service.get_task(db, task_id))
    runner.submit(task.id)
    return task_service.task_summary(task)
