from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.models.enums import CampaignStatus, CampaignType, EmailMessageStatus
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.marketing import (
    AudienceFilter, CampaignApprovalIn, CampaignCreate, CampaignStatusIn, CampaignUpdate, GenerateIn, MetricsIn,
)
from app.security.deps import get_current_user, require_writer
from app.services import campaign_service, email_sending

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("")
def list_campaigns(q: str | None = Query(None, max_length=200), status_filter: CampaignStatus | None = Query(None, alias="status"),
                   campaign_type: CampaignType | None = None, client_id: int | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                   db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    items, total = campaign_service.search(db, q=q, status=status_filter, campaign_type=campaign_type,
                                           client_id=client_id, offset=(page - 1) * page_size, limit=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: CampaignCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.create(db, user, body.model_dump())
    db.commit()
    return campaign_service.serialize(db, c, full=True)


@router.get("/{campaign_id}")
def get(campaign_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return campaign_service.serialize(db, campaign_service.get(db, campaign_id), full=True)


@router.patch("/{campaign_id}")
def update(campaign_id: int, body: CampaignUpdate, db: Session = Depends(get_db),
           user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.update(db, user, campaign_service.get(db, campaign_id), body.model_dump(exclude_unset=True))
    db.commit()
    return campaign_service.serialize(db, c, full=True)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    campaign_service.delete(db, user, campaign_service.get(db, campaign_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{campaign_id}/audience")
def set_audience(campaign_id: int, body: AudienceFilter, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> dict[str, Any]:
    summary = campaign_service.set_audience(db, user, campaign_service.get(db, campaign_id), body.model_dump(mode="json"))
    db.commit()
    return summary


@router.post("/{campaign_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(campaign_id: int, body: GenerateIn, db: Session = Depends(get_db), user: User = Depends(require_writer),
             runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    task = campaign_service.generate(db, user, campaign_service.get(db, campaign_id), body.kind, body.notes)
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}


@router.post("/{campaign_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
def analyze(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer),
            runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    task = campaign_service.analyze(db, user, campaign_service.get(db, campaign_id))
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}


@router.post("/{campaign_id}/import/{task_id}")
def import_task(campaign_id: int, task_id: int, indexes: list[int] | None = Body(default=None, embed=True),
                db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.get(db, campaign_id)
    result = campaign_service.import_task(db, user, c, task_id, indexes)
    db.commit()
    return {**result, "campaign": campaign_service.serialize(db, c, full=True)}


@router.delete("/{campaign_id}/drafts/{index}", status_code=status.HTTP_204_NO_CONTENT)
def remove_draft(campaign_id: int, index: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    campaign_service.remove_draft(db, user, campaign_service.get(db, campaign_id), index)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{campaign_id}/request-approval")
def request_approval(campaign_id: int, body: CampaignApprovalIn, db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings),
                     user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.get(db, campaign_id)
    approval = campaign_service.request_approval(db, user, c, body.model_dump(), settings)
    db.commit()
    return {"approval_id": approval.id, "campaign": campaign_service.serialize(db, c, full=True)}


@router.post("/{campaign_id}/status")
def change_status(campaign_id: int, body: CampaignStatusIn, db: Session = Depends(get_db),
                  user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.change_status(db, user, campaign_service.get(db, campaign_id), CampaignStatus(body.status), body.note)
    db.commit()
    return campaign_service.serialize(db, c, full=True)


@router.get("/{campaign_id}/outbox")
def outbox(campaign_id: int, status_filter: EmailMessageStatus | None = Query(None, alias="status"),
           page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
           db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    """Every rendered email of this campaign, exactly as it was (or will be) sent."""
    c = campaign_service.get(db, campaign_id)
    items, total = email_sending.list_messages(db, c.id, status_filter, (page - 1) * page_size, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "counts": email_sending.counts(db, c.id)}


@router.put("/{campaign_id}/metrics")
def update_metrics(campaign_id: int, body: MetricsIn, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> dict[str, Any]:
    c = campaign_service.update_metrics(db, user, campaign_service.get(db, campaign_id), body.model_dump())
    db.commit()
    return campaign_service.serialize(db, c, full=True)
