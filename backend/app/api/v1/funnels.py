import re
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.marketing import funnel_templates, landing_page
from app.models.enums import FunnelStatus
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.marketing import FunnelCreate, FunnelPublishedIn, FunnelUpdate
from app.security.deps import get_current_user, require_writer
from app.services import funnel_service

router = APIRouter(prefix="/funnels", tags=["funnels"])

# The preview is our own generated HTML, shown in a sandboxed iframe: inline CSS only, no scripts,
# no network requests, and the form cannot submit anywhere.
PREVIEW_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; "
               "base-uri 'none'; frame-ancestors 'self'")


@router.get("/templates")
def templates(_: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": funnel_templates.summary()}


@router.get("")
def list_funnels(q: str | None = Query(None, max_length=200), status_filter: FunnelStatus | None = Query(None, alias="status"),
                 client_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    items, total = funnel_service.search(db, q=q, status=status_filter, client_id=client_id,
                                         offset=(page - 1) * page_size, limit=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: FunnelCreate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    f = funnel_service.create(db, user, body.model_dump(exclude_unset=True, by_alias=True))
    db.commit()
    return funnel_service.serialize(db, f, full=True)


@router.get("/{funnel_id}")
def get(funnel_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return funnel_service.serialize(db, funnel_service.get(db, funnel_id), full=True)


@router.patch("/{funnel_id}")
def update(funnel_id: int, body: FunnelUpdate, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    f = funnel_service.update(db, user, funnel_service.get(db, funnel_id), body.model_dump(exclude_unset=True, by_alias=True))
    db.commit()
    return funnel_service.serialize(db, f, full=True)


@router.delete("/{funnel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(funnel_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    funnel_service.delete(db, user, funnel_service.get(db, funnel_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{funnel_id}/preview", include_in_schema=False)
def preview(funnel_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Response:
    f = funnel_service.get(db, funnel_id)
    return Response(landing_page.render(f, preview=True), media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": PREVIEW_CSP, "X-Frame-Options": "SAMEORIGIN"})


@router.get("/{funnel_id}/download")
def download(funnel_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Response:
    f = funnel_service.get(db, funnel_id)
    slug = re.sub(r"[^a-z0-9]+", "-", f.name.lower()).strip("-") or "landing-page"
    return Response(landing_page.render(f), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.html"'})


@router.post("/{funnel_id}/request-approval")
def request_approval(funnel_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    f = funnel_service.get(db, funnel_id)
    approval = funnel_service.request_publish(db, user, f)
    db.commit()
    return {"approval_id": approval.id, "funnel": funnel_service.serialize(db, f, full=True)}


@router.post("/{funnel_id}/published")
def mark_published(funnel_id: int, body: FunnelPublishedIn, db: Session = Depends(get_db),
                   user: User = Depends(require_writer)) -> dict[str, Any]:
    f = funnel_service.mark_published(db, user, funnel_service.get(db, funnel_id), body.landing_page_url)
    db.commit()
    return funnel_service.serialize(db, f, full=True)


@router.post("/{funnel_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(funnel_id: int, notes: str | None = Body(default=None, embed=True, max_length=2000),
             db: Session = Depends(get_db), user: User = Depends(require_writer),
             runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    task = funnel_service.generate_copy(db, user, funnel_service.get(db, funnel_id), notes)
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}


@router.post("/{funnel_id}/import/{task_id}")
def import_copy(funnel_id: int, task_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    f = funnel_service.get(db, funnel_id)
    result = funnel_service.import_copy(db, user, f, task_id)
    db.commit()
    return {**result, "funnel": funnel_service.serialize(db, f, full=True)}
