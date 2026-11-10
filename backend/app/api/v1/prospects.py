from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import SalesStage, TaskPriority
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.crm import ActivityCreate, AgentResearchIn, ConvertProspect, ProspectCreate, ProspectUpdate
from app.security.deps import get_current_user, require_writer
from app.services import agent_service, client_service, prospect_service, task_service
from app.utils.errors import AppError

router = APIRouter(prefix="/prospects", tags=["prospects"])

SortField = Literal["company_name", "lead_score", "created_at", "updated_at", "next_follow_up_on", "stage", "city"]


def _filters(
    q: str | None = Query(None, max_length=200),
    stage: SalesStage | None = None,
    industry: str | None = Query(None, max_length=100),
    city: str | None = Query(None, max_length=100),
    min_score: int | None = Query(None, ge=0, le=100),
    follow_up_due: bool = False,
    sort: SortField = "created_at",
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    return {"q": q, "stage": stage, "industry": industry, "city": city, "min_score": min_score,
            "follow_up_due": follow_up_due, "sort": sort, "order": order}


@router.get("")
def list_prospects(filters: dict = Depends(_filters), page: int = Query(1, ge=1),
                   page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)) -> dict[str, Any]:
    rows, total = prospect_service.search(db, **filters, offset=(page - 1) * page_size, limit=page_size)
    return {"items": [prospect_service.serialize(p) for p in rows], "total": total, "page": page,
            "page_size": page_size, "facets": prospect_service.facets(db)}


@router.get("/export")
def export_prospects(filters: dict = Depends(_filters), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> Response:
    rows, _total = prospect_service.search(db, **filters, offset=0, limit=10_000)
    # Exports contain personal data: record who exported what.
    from app.models.enums import ActorType
    from app.services import audit_service

    audit_service.record(db, "prospect.exported", user=user, actor_type=ActorType.USER, entity_type="prospect",
                         summary=f"Exported {len(rows)} prospect(s) to CSV")
    db.commit()
    return Response(
        content=prospect_service.export_csv(rows), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="prospects-{date.today().isoformat()}.csv"'},
    )


@router.post("/import")
async def import_prospects(file: UploadFile = File(...), skip_duplicates: bool = True,
                           db: Session = Depends(get_db), user: User = Depends(require_writer)) -> dict[str, Any]:
    name = (file.filename or "").lower()
    if not name.endswith(".csv"):
        raise AppError("Upload a .csv file")
    raw = await file.read(prospect_service.MAX_IMPORT_BYTES + 1)
    result = prospect_service.import_csv(db, user, raw, skip_duplicates=skip_duplicates)
    db.commit()
    return result


@router.post("/check-duplicates")
def check_duplicates(body: ProspectUpdate, db: Session = Depends(get_db),
                     _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"duplicates": prospect_service.find_duplicates(db, body.model_dump())}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_prospect(body: ProspectCreate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> dict[str, Any]:
    p = prospect_service.create(db, user, body.model_dump(exclude={"allow_duplicate"}),
                                allow_duplicate=body.allow_duplicate)
    db.commit()
    return prospect_service.serialize(p)


@router.get("/{prospect_id}")
def get_prospect(prospect_id: int, db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)) -> dict[str, Any]:
    p = prospect_service.get(db, prospect_id)
    client = client_service.for_prospect(db, p.id)
    return {**prospect_service.serialize(p), "activities": prospect_service.activities(db, p),
            "client_id": client.id if client else None}


@router.patch("/{prospect_id}")
def update_prospect(prospect_id: int, body: ProspectUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> dict[str, Any]:
    p = prospect_service.update(db, user, prospect_service.get(db, prospect_id), body.model_dump(exclude_unset=True))
    db.commit()
    return prospect_service.serialize(p)


@router.delete("/{prospect_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prospect(prospect_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer)) -> Response:
    prospect_service.soft_delete(db, user, prospect_service.get(db, prospect_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{prospect_id}/activities", status_code=status.HTTP_201_CREATED)
def add_activity(prospect_id: int, body: ActivityCreate, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> dict[str, Any]:
    p = prospect_service.get(db, prospect_id)
    prospect_service.add_activity(db, user, p, body.activity_type, body.description, body.mark_contacted)
    db.commit()
    return {"activities": prospect_service.activities(db, p)}


@router.post("/{prospect_id}/convert", status_code=status.HTTP_201_CREATED)
def convert(prospect_id: int, body: ConvertProspect, db: Session = Depends(get_db),
            user: User = Depends(require_writer)) -> dict[str, Any]:
    client = prospect_service.convert_to_client(db, user, prospect_service.get(db, prospect_id), body.model_dump())
    db.commit()
    return client_service.serialize(client)


@router.post("/{prospect_id}/research", status_code=status.HTTP_202_ACCEPTED)
def research(prospect_id: int, body: AgentResearchIn, db: Session = Depends(get_db),
             user: User = Depends(require_writer), runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Ask the Sales Agent to research this record. The record is passed as untrusted data."""
    p = prospect_service.get(db, prospect_id)
    agent_service.ensure_seeded(db)
    sales = agent_service.get_by_key(db, "sales")
    record = jsonable_encoder({k: v for k, v in prospect_service.serialize(p).items()
              if k not in ("normalized_domain", "created_at", "updated_at") and v not in (None, "")})
    task = task_service.create_task(
        db, user, agent_id=sales.id, title=f"Research prospect: {p.company_name}"[:300],
        instructions=body.instructions, priority=TaskPriority.NORMAL,
        context={"prospects": [record]}, task_type="prospect_research",
    )
    prospect_service._log(db, p, "agent_task", f"Sales Agent asked to research (task #{task.id})", user, sales.id)
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}
