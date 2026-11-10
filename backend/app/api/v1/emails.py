from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.marketing import compliance
from app.models.enums import EmailKind
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.marketing import (
    AudienceFilter, EmailCheckIn, EmailCreate, EmailGenerateIn, EmailUpdate, RecipientsCheckIn, SuppressionIn,
)
from app.security.deps import get_current_user, require_admin, require_writer
from app.services import campaign_service, drafting, email_service
from app.utils.errors import AppError

router = APIRouter(prefix="/emails", tags=["email"])


@router.get("/templates")
def list_templates(campaign_id: int | None = None, kind: EmailKind | None = None, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [email_service.serialize(t) for t in email_service.list_templates(db, campaign_id, kind)]}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(body: EmailCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    t = email_service.create(db, user, body.model_dump())
    db.commit()
    return email_service.serialize(t)


@router.get("/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return email_service.serialize(email_service.get(db, template_id))


@router.patch("/templates/{template_id}")
def update_template(template_id: int, body: EmailUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> dict[str, Any]:
    t = email_service.update(db, user, email_service.get(db, template_id), body.model_dump(exclude_unset=True))
    db.commit()
    return email_service.serialize(t)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    email_service.delete(db, user, email_service.get(db, template_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/templates/{template_id}/preview")
def preview(template_id: int, prospect_id: int | None = None, db: Session = Depends(get_db),
            settings: Settings = Depends(get_settings), _: User = Depends(get_current_user)) -> dict[str, Any]:
    """Personalised rendering for one prospect. Missing values are shown, never invented."""
    return email_service.preview(db, settings, email_service.get(db, template_id), prospect_id)


@router.post("/check")
def check(body: EmailCheckIn, _: User = Depends(get_current_user)) -> dict[str, Any]:
    issues = compliance.check_email(body.subject, body.body_text)
    return {"issues": issues, "compliant": not compliance.has_errors(issues)}


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(body: EmailGenerateIn, db: Session = Depends(get_db), user: User = Depends(require_writer),
             runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    if body.campaign_id is not None:
        campaign = campaign_service.get(db, body.campaign_id)
        task = campaign_service.generate(db, user, campaign, "emails", body.brief)
    else:
        what = {
            "sequence": "Write a 3-step email follow-up sequence. One deliverable per email (kind email_draft), "
                        "content starting with 'Subject: ...'.",
            "subject_lines": "Write 10 subject-line options as one deliverable (kind report), one per line. "
                             "No misleading 'Re:'/'Fwd:' prefixes, no spam phrases.",
            "single": "Write one email (kind email_draft) whose content starts with 'Subject: ...'.",
        }[body.purpose]
        rules = (" Every email must include {{sender_name}}, {{agency_address}} and {{unsubscribe_link}}. Use merge "
                 "fields like {{first_name}} and {{company_name}} instead of real recipient details.")
        task = drafting.start(db, user, agent_key="email_marketing", title=f"Email draft: {body.brief[:80]}",
                              instructions=f"{what}{rules}\n\nBrief: {body.brief}", context={},
                              task_type="email_generate", entity_type="email_library", entity_id=0)
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}


@router.get("/ai-tasks")
def library_tasks(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [drafting.task_brief(t) for t in drafting.linked_tasks(db, "email_library", 0)]}


@router.post("/import/{task_id}", status_code=status.HTTP_201_CREATED)
def import_library_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    task, deliverables = drafting.finished_deliverables(db, task_id, "email_library", 0)
    emails = [d for d in deliverables if d.get("kind") == "email_draft"]
    if not emails:
        raise AppError("This task produced no email drafts to import")
    created = []
    for i, d in enumerate(emails):
        subject, body = drafting.split_email(d)
        created.append(email_service.create(db, user, {"name": d.get("title") or f"Email {i + 1}", "subject": subject,
                                                       "body_text": body, "kind": EmailKind.TEMPLATE}))
    db.commit()
    return {"items": [email_service.serialize(t) for t in created]}


# -- audience & suppression --------------------------------------------------------------------


@router.post("/segments/preview")
def segment_preview(body: AudienceFilter, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return email_service.segment(db, body.model_dump(mode="json"))


@router.post("/recipients/check")
def recipients_check(body: RecipientsCheckIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return email_service.check_recipients(db, body.emails)


@router.get("/suppressions")
def list_suppressions(q: str | None = Query(None, max_length=255), page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                      _: User = Depends(get_current_user)) -> dict[str, Any]:
    rows, total = email_service.list_suppressions(db, q, (page - 1) * page_size, page_size)
    return {"items": jsonable_encoder([{"id": s.id, "email": s.email, "reason": s.reason.value, "source": s.source,
                                        "created_at": s.created_at} for s in rows]),
            "total": total, "page": page, "page_size": page_size}


@router.post("/suppressions", status_code=status.HTTP_201_CREATED)
def add_suppression(body: SuppressionIn, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    s = email_service.add_suppression(db, body.email, body.reason, body.source or "manual", user)
    db.commit()
    return {"id": s.id, "email": s.email, "reason": s.reason.value}


@router.delete("/suppressions/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_suppression(suppression_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> Response:
    email_service.remove_suppression(db, user, suppression_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings")
def sender_settings(settings: Settings = Depends(get_settings), _: User = Depends(get_current_user)) -> dict[str, Any]:
    """Sender identity used in previews (non-secret)."""
    return {"agency_name": settings.agency_name, "agency_address": settings.agency_address,
            "sender_name": settings.sender_name, "sender_email": settings.sender_email,
            "configured": bool(settings.agency_name and settings.agency_address and settings.sender_name),
            "sending_enabled": False}
