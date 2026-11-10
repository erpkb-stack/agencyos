from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.security.deps import get_current_user, require_writer
from app.services import drafting, report_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/reports")
def list_reports(_: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [{"key": k, "title": t, "default_period": dict(zip(("start", "end"), report_service.default_period(k)))}
                      for k, t in report_service.REPORTS.items()]}


@router.get("/reports/{key}")
def get_report(key: str, start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)) -> dict[str, Any]:
    return report_service.build(db, key, start, end)


@router.get("/reports/{key}/csv")
def report_csv(key: str, start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)) -> Response:
    report = report_service.build(db, key, start, end)
    filename = f"{key}-{report['period']['start']}-{report['period']['end']}.csv"
    return Response(report_service.to_csv(report), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/reports/{key}/summarize", status_code=status.HTTP_202_ACCEPTED)
def summarize(key: str, start: date | None = None, end: date | None = None, db: Session = Depends(get_db),
              user: User = Depends(require_writer), runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Ask the Analytics Agent for a written summary of the report. It sees only the report data."""
    report = report_service.build(db, key, start, end)
    task = drafting.start(
        db, user, agent_key="analytics", title=f"{report['title']} {report['period']['start']}–{report['period']['end']}",
        instructions="Write a concise summary of this report for the Founder: what changed, what stands out, risks, "
                     "and 3–5 recommended actions. Use only the figures in the report; say when data is missing.",
        context={"report": jsonable_encoder(report)}, task_type="report_summary", entity_type="report", entity_id=0,
    )
    db.commit()
    runner.submit(task.id)
    return {"task_id": task.id}


@router.get("/summaries")
def summaries(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": [drafting.task_brief(t) for t in drafting.linked_tasks(db, "report", 0)]}
