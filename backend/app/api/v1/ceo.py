from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.ai import CEOMessageIn, ConversationRename
from app.security.deps import get_current_user, require_writer
from app.services import task_service

router = APIRouter(prefix="/ceo", tags=["ai-ceo"])


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": task_service.list_conversations(db, user)}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    return task_service.get_conversation(db, user, conversation_id)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, body: ConversationRename, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> dict[str, Any]:
    c = task_service.rename_conversation(db, user, conversation_id, body.title)
    return {"id": c.id, "title": c.title}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_writer)) -> Response:
    task_service.delete_conversation(db, user, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
def send_message(body: CEOMessageIn, db: Session = Depends(get_db), user: User = Depends(require_writer),
                 runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Accepted for processing. Poll ``GET /tasks/{task_id}`` until it is no longer running."""
    created = task_service.send_ceo_message(db, user, body.content, body.conversation_id)
    runner.submit(created["task_id"])
    return created
