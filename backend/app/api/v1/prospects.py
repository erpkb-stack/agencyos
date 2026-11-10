from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.enums import SalesStage, TaskPriority
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.schemas.crm import ActivityCreate, AgentResearchIn, ConvertProspect, ProspectCreate, ProspectUpdate
from app.security.deps import get_current_user, require_writer
from app.config.settings import Settings, get_settings
from app.services import agent_service, client_service, prospect_service, research_service, task_service
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


def _site_summary(c) -> dict[str, Any] | None:
    if c is None:
        return None
    s = c.signals or {}
    return {"status": c.status.value, "score": s.get("conversion_gap_score"),
            "issues": len(s.get("conversion_flags", [])) if c.status.value == "DONE" else None,
            "checked_at": c.finished_at}


@router.get("")
def list_prospects(filters: dict = Depends(_filters), page: int = Query(1, ge=1),
                   page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)) -> dict[str, Any]:
    rows, total = prospect_service.search(db, **filters, offset=(page - 1) * page_size, limit=page_size)
    checks = research_service.latest_by_prospect(db, [p.id for p in rows])
    return {"items": [{**prospect_service.serialize(p), "website_check": _site_summary(checks.get(p.id))} for p in rows],
            "total": total, "page": page, "page_size": page_size, "facets": prospect_service.facets(db)}


class BulkCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prospect_ids: list[int] | None = Field(default=None, max_length=500)


@router.post("/website-checks", status_code=status.HTTP_202_ACCEPTED)
def bulk_website_checks(body: BulkCheckIn, filters: dict = Depends(_filters), db: Session = Depends(get_db),
                        user: User = Depends(require_writer), settings: Settings = Depends(get_settings),
                        runner: research_service.ResearchRunner = Depends(research_service.get_research_runner)) -> dict[str, Any]:
    """Queue website checks for the given prospects, or for everything matching the list filters."""
    if body.prospect_ids is None:
        rows, _total = prospect_service.search(db, **filters, offset=0, limit=settings.web_research_bulk_limit)
        ids = [p.id for p in rows]
    else:
        ids = body.prospect_ids
    return research_service.queue_checks(db, user, ids, settings, runner)


class ConversionReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prospect_ids: list[int] | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/conversion-review", status_code=status.HTTP_202_ACCEPTED)
def conversion_review(body: ConversionReviewIn, db: Session = Depends(get_db), user: User = Depends(require_writer),
                      runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Ask the Sales Agent to rank checked prospects by likely lead-conversion problems."""
    from sqlalchemy import select

    from app.models.crm import Prospect

    query = select(Prospect).where(Prospect.deleted_at.is_(None))
    if body.prospect_ids:
        query = query.where(Prospect.id.in_(body.prospect_ids))
    prospects = list(db.scalars(query))
    checks = research_service.latest_by_prospect(db, [p.id for p in prospects])
    checked = [(p, checks[p.id]) for p in prospects if p.id in checks and checks[p.id].status.value == "DONE"]
    if not checked:
        raise AppError("No prospects with a completed website check; check some websites first")
    checked.sort(key=lambda pc: -(pc[1].signals or {}).get("conversion_gap_score", 0))
    records = [jsonable_encoder({"company_name": p.company_name, "industry": p.industry, "city": p.city,
                                 "stage": p.stage.value, "lead_score": p.lead_score,
                                 "website_check": research_service.agent_view(c)}) for p, c in checked[: body.limit]]
    agent_service.ensure_seeded(db)
    sales = agent_service.get_by_key(db, "sales")
    task = task_service.create_task(
        db, user, agent_id=sales.id, title=f"Lead-conversion review of {len(records)} prospect website(s)",
        instructions=("Rank these prospects by how much their website likely costs them leads, using only the "
                      "website facts provided. For each, name the most important problems and the service we could "
                      "offer to fix them. Do not invent any business details, contact details or metrics."),
        priority=TaskPriority.NORMAL, context={"prospects": records}, task_type="conversion_review",
    )
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id, "prospects": len(records)}


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
            "client_id": client.id if client else None,
            "website_check": research_service.serialize(research_service.latest(db, p.id))}


@router.post("/{prospect_id}/website-check")
def website_check(prospect_id: int, db: Session = Depends(get_db), user: User = Depends(require_writer),
                  settings: Settings = Depends(get_settings),
                  fetcher=Depends(research_service.get_fetcher)) -> dict[str, Any]:
    """Check this prospect's public website now (takes a few seconds)."""
    c = research_service.check_now(db, user, prospect_service.get(db, prospect_id), fetcher, settings)
    return research_service.serialize(c)


@router.get("/{prospect_id}/website-checks")
def website_checks(prospect_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.crm import WebsiteCheck

    prospect_service.get(db, prospect_id)
    rows = db.scalars(select(WebsiteCheck).where(WebsiteCheck.prospect_id == prospect_id)
                      .order_by(WebsiteCheck.id.desc()).limit(20))
    return {"items": [research_service.serialize(c) for c in rows]}


@router.post("/{prospect_id}/apply-website-findings")
def apply_website_findings(prospect_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_writer)) -> dict[str, Any]:
    p = research_service.apply_findings(db, user, prospect_service.get(db, prospect_id))
    return prospect_service.serialize(p)


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
    site = research_service.agent_view(research_service.latest(db, p.id, done_only=True))
    if site:
        record["website_check"] = jsonable_encoder(site)
    task = task_service.create_task(
        db, user, agent_id=sales.id, title=f"Research prospect: {p.company_name}"[:300],
        instructions=body.instructions, priority=TaskPriority.NORMAL,
        context={"prospects": [record]}, task_type="prospect_research",
    )
    prospect_service._log(db, p, "agent_task", f"Sales Agent asked to research (task #{task.id})", user, sales.id)
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}
