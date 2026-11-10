"""Prospect website research: run checks (one now, or many in the background), store the facts,
and turn them into prospect observations or agent context. Contact details found on a site are
never copied into records; only whether they are present."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.integrations import web_research as wr
from app.models.crm import Prospect, ProspectActivity, WebsiteCheck
from app.models.enums import ActorType, SalesStage, WebsiteCheckStatus as WS
from app.models.identity import User
from app.services import audit_service
from app.utils.errors import AppError, NotFoundError
from app.utils.time import utcnow

logger = logging.getLogger("agencyos.research")

FetcherFactory = Callable[[], wr.Fetcher]


def default_fetcher_factory(settings: Settings | None = None) -> wr.Fetcher:
    settings = settings or get_settings()
    client = httpx.Client(timeout=settings.web_research_timeout_seconds)
    return wr.Fetcher(client, user_agent=settings.research_user_agent, max_bytes=settings.web_research_max_bytes,
                      min_interval=settings.web_research_min_interval_seconds)


# -- running a check ---------------------------------------------------------------------------


def _check_out(c: WebsiteCheck | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "prospect_id": c.prospect_id, "status": c.status.value, "url": c.url, "final_url": c.final_url,
            "http_status": c.http_status, "title": c.title, "description": c.description, "signals": c.signals,
            "pages": c.pages, "text_excerpt": c.text_excerpt, "error": c.error, "duration_ms": c.duration_ms,
            "created_at": c.created_at, "finished_at": c.finished_at}


serialize = _check_out


def latest(db: Session, prospect_id: int, *, done_only: bool = False) -> WebsiteCheck | None:
    q = select(WebsiteCheck).where(WebsiteCheck.prospect_id == prospect_id)
    if done_only:
        q = q.where(WebsiteCheck.status == WS.DONE)
    return db.scalar(q.order_by(WebsiteCheck.id.desc()).limit(1))


def execute(db: Session, check: WebsiteCheck, fetcher: wr.Fetcher, settings: Settings) -> WebsiteCheck:
    """Fetch and analyse the site for an existing check row, then record the outcome."""
    check.status = WS.RUNNING
    db.commit()
    started = time.monotonic()
    try:
        result = wr.research_site(check.url, fetcher, settings.web_research_max_pages)
    except wr.BlockedError as exc:
        check.status, check.error = WS.BLOCKED, str(exc)
    except wr.FetchError as exc:
        check.status, check.error = WS.ERROR, str(exc)
    except Exception:  # noqa: BLE001 - unexpected parser/network failures are recorded, not raised
        logger.exception("Website check %s failed", check.id)
        check.status, check.error = WS.ERROR, "Unexpected error while checking the website"
    else:
        check.status = WS.DONE
        check.final_url, check.http_status = result.final_url[:500], result.http_status
        check.title, check.description = result.title or None, result.description or None
        check.signals, check.pages, check.text_excerpt = result.signals, result.pages, result.text_excerpt
    check.duration_ms = int((time.monotonic() - started) * 1000)
    check.finished_at = utcnow()
    summary = (f"Website checked: {len(check.signals['conversion_flags'])} possible lead-conversion issue(s)"
               if check.status == WS.DONE else f"Website check {check.status.value.lower()}: {check.error}")
    db.add(ProspectActivity(prospect_id=check.prospect_id, activity_type="website_check", description=summary,
                            user_id=check.requested_by_user_id))
    db.commit()
    return check


def _new_check(db: Session, user: User, p: Prospect) -> WebsiteCheck:
    if not p.website:
        raise AppError("This prospect has no website to check")
    c = WebsiteCheck(prospect_id=p.id, requested_by_user_id=user.id, url=p.website, status=WS.QUEUED)
    db.add(c)
    db.flush()
    return c


def check_now(db: Session, user: User, p: Prospect, fetcher: wr.Fetcher, settings: Settings) -> WebsiteCheck:
    if not settings.web_research_enabled:
        raise AppError("Website research is turned off (WEB_RESEARCH_ENABLED=false)")
    c = _new_check(db, user, p)
    audit_service.record(db, "prospect.website_checked", user=user, actor_type=ActorType.USER,
                         entity_type="prospect", entity_id=p.id, summary=f"Checked website of {p.company_name}")
    db.commit()
    return execute(db, c, fetcher, settings)


# -- background (bulk) -------------------------------------------------------------------------


class ResearchRunner(Protocol):
    def submit(self, check_id: int) -> None: ...


class InlineResearchRunner:
    def __init__(self, session_factory: Callable[[], Session], fetcher_factory: FetcherFactory, settings: Settings):
        self.session_factory, self.fetcher_factory, self.settings = session_factory, fetcher_factory, settings

    def submit(self, check_id: int) -> None:
        run_check(self.session_factory, check_id, self.fetcher_factory(), self.settings)


class ThreadResearchRunner(InlineResearchRunner):
    def __init__(self, session_factory, fetcher_factory, settings) -> None:
        super().__init__(session_factory, fetcher_factory, settings)
        self._fetcher = fetcher_factory()  # shared: per-site rate limits and robots cache apply across checks
        self._pool = ThreadPoolExecutor(max_workers=settings.web_research_concurrency,
                                        thread_name_prefix="agencyos-research")

    def submit(self, check_id: int) -> None:
        self._pool.submit(run_check, self.session_factory, check_id, self._fetcher, self.settings)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def run_check(session_factory: Callable[[], Session], check_id: int, fetcher: wr.Fetcher, settings: Settings) -> None:
    with session_factory() as db:
        c = db.get(WebsiteCheck, check_id)
        if c is None or c.status != WS.QUEUED:
            return
        execute(db, c, fetcher, settings)


_runner: ThreadResearchRunner | None = None
_lock = threading.Lock()


def get_research_runner() -> ResearchRunner:
    global _runner
    with _lock:
        if _runner is None:
            from app.database.session import SessionLocal

            _runner = ThreadResearchRunner(SessionLocal, default_fetcher_factory, get_settings())
        return _runner


def get_fetcher() -> wr.Fetcher:
    return default_fetcher_factory()


def queue_checks(db: Session, user: User, prospect_ids: list[int], settings: Settings,
                 runner: ResearchRunner) -> dict[str, Any]:
    if not settings.web_research_enabled:
        raise AppError("Website research is turned off (WEB_RESEARCH_ENABLED=false)")
    ids = list(dict.fromkeys(prospect_ids))[: settings.web_research_bulk_limit]
    prospects = list(db.scalars(select(Prospect).where(Prospect.id.in_(ids), Prospect.deleted_at.is_(None))))
    queued, skipped = [], []
    for p in prospects:
        if not p.website:
            skipped.append({"id": p.id, "company_name": p.company_name, "reason": "no website"})
            continue
        current = latest(db, p.id)
        if current is not None and current.status in (WS.QUEUED, WS.RUNNING):
            skipped.append({"id": p.id, "company_name": p.company_name, "reason": "already being checked"})
            continue
        queued.append(_new_check(db, user, p).id)
    audit_service.record(db, "prospect.websites_queued", user=user, actor_type=ActorType.USER, entity_type="prospect",
                         summary=f"Queued website checks for {len(queued)} prospect(s)")
    db.commit()
    for check_id in queued:
        runner.submit(check_id)
    return {"queued": len(queued), "skipped": skipped, "limit": settings.web_research_bulk_limit}


def resume_queued(db: Session) -> int:
    """At start-up: interrupted checks go back to QUEUED and are resubmitted."""
    from sqlalchemy import inspect

    if not inspect(db.get_bind()).has_table("prospect_website_checks"):
        return 0
    rows = list(db.scalars(select(WebsiteCheck).where(WebsiteCheck.status.in_([WS.QUEUED, WS.RUNNING]))))
    for c in rows:
        c.status = WS.QUEUED
    db.commit()
    runner = get_research_runner()
    for c in rows:
        runner.submit(c.id)
    return len(rows)


def status_counts(db: Session, prospect_ids: list[int]) -> dict[str, int]:
    rows = db.execute(select(WebsiteCheck.status, func.count()).where(WebsiteCheck.prospect_id.in_(prospect_ids))
                      .group_by(WebsiteCheck.status)).all()
    return {s.value: n for s, n in rows}


# -- using the results -------------------------------------------------------------------------


def latest_by_prospect(db: Session, prospect_ids: list[int]) -> dict[int, WebsiteCheck]:
    if not prospect_ids:
        return {}
    newest = (select(WebsiteCheck.prospect_id, func.max(WebsiteCheck.id).label("mid"))
              .where(WebsiteCheck.prospect_id.in_(prospect_ids)).group_by(WebsiteCheck.prospect_id).subquery())
    rows = db.scalars(select(WebsiteCheck).join(newest, WebsiteCheck.id == newest.c.mid))
    return {c.prospect_id: c for c in rows}


def agent_view(c: WebsiteCheck | None) -> dict[str, Any] | None:
    """What an agent may see: facts and a short excerpt. Never instructions; never contact values."""
    if c is None or c.status != WS.DONE:
        return None
    s = c.signals or {}
    return {"checked_at": c.finished_at.isoformat() if c.finished_at else None, "url": c.final_url,
            "title": c.title, "description": c.description,
            "facts": {k: v for k, v in s.items() if k not in ("conversion_flags",)},
            "possible_conversion_issues": [f["issue"] for f in s.get("conversion_flags", [])],
            "page_text_excerpt": (c.text_excerpt or "")[:3000]}


def apply_findings(db: Session, user: User, p: Prospect) -> Prospect:
    c = latest(db, p.id, done_only=True)
    if c is None:
        raise NotFoundError("No completed website check for this prospect")
    flags = [f["issue"] for f in (c.signals or {}).get("conversion_flags", [])]
    date = c.finished_at.date().isoformat() if c.finished_at else ""
    note = f"Website check {date}: " + ("; ".join(flags) if flags else "no obvious lead-conversion issues found") + "."
    p.observations = f"{p.observations}\n{note}".strip() if p.observations else note
    changes = "observations"
    if p.stage == SalesStage.NEW:
        p.stage = SalesStage.RESEARCHED
        changes += ", stage NEW → RESEARCHED"
    db.add(ProspectActivity(prospect_id=p.id, activity_type="updated", description=f"Website findings added ({changes})",
                            user_id=user.id))
    audit_service.record(db, "prospect.updated", user=user, actor_type=ActorType.USER, entity_type="prospect",
                         entity_id=p.id, summary=f"Added website findings to {p.company_name}")
    db.commit()
    return p
