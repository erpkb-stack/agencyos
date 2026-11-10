from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import ApprovalStatus
from app.models.identity import User
from app.schemas.ai import ApprovalDecisionIn
from app.security.deps import get_current_user, require_admin
from app.services import task_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
def list_approvals(
    status_filter: ApprovalStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows, total = task_service.list_approvals(db, status_filter, (page - 1) * page_size, page_size)
    return {"items": [task_service.approval_out(db, a) for a in rows], "total": total,
            "page": page, "page_size": page_size}


@router.post("/{approval_id}/decision")
def decide(approval_id: int, body: ApprovalDecisionIn, db: Session = Depends(get_db),
           user: User = Depends(require_admin)) -> dict[str, Any]:
    """Record the Founder's decision. No external integration exists yet, so approving executes nothing."""
    approval = task_service.decide_approval(db, user, approval_id, ApprovalStatus(body.decision), body.comments)
    return {**task_service.approval_out(db, approval), "executed": False,
            "note": "Decision recorded. No external integration is connected, so nothing was sent or published."}
