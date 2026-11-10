"""AgencyOS FastAPI application: REST API under /api/v1 and the static frontend at /."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.config.settings import APP_VERSION, FRONTEND_DIR, get_settings
from app.middleware import RequestContextMiddleware
from app.orchestrator import shutdown_runner, start_runner
from app.utils.errors import NotFoundError, register_exception_handlers
from app.utils.logging import configure_logging


def _startup_housekeeping(logger: logging.Logger) -> None:
    """Seed built-in agents and fail tasks a previous process left mid-run."""
    from sqlalchemy import inspect

    from app.database.session import SessionLocal
    from app.orchestrator.runner import recover_interrupted
    from app.services import agent_service

    try:
        with SessionLocal() as db:
            if not inspect(db.get_bind()).has_table("agents"):
                logger.warning("Database is not initialised; run `python -m app.cli init-db`")
                return
            added = agent_service.ensure_seeded(db)
            recovered = recover_interrupted(db)
            from app.services import research_service

            resumed = research_service.resume_queued(db)
            if resumed:
                logger.info(f"Startup: resumed {resumed} website check(s)")
        if added or any(recovered.values()):
            logger.info(f"Startup: {added} built-in agents added; interrupted tasks: "
                        f"{recovered['requeued']} re-queued, {recovered['failed']} marked failed")
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("Startup housekeeping failed")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("agencyos")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            f"AgencyOS {APP_VERSION} starting (env={settings.app_env.value}, "
            f"db={settings.database_backend}, llm={settings.llm_provider.value}, "
            f"demo_login={settings.demo_login_enabled})"
        )
        for warning in settings.production_warnings():
            logger.warning(f"Configuration: {warning}")
        _startup_housekeeping(logger)
        start_runner()  # resume queued agent work right away (no-op if the worker runs separately)
        yield
        shutdown_runner()
        logger.info("AgencyOS stopped")

    app = FastAPI(
        title="AgencyOS API",
        version=APP_VERSION,
        description="Internal AI agency operating system. All endpoints under /api/v1.",
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )
    app.add_middleware(
        RequestContextMiddleware,
        max_request_bytes=settings.max_request_bytes,
        hsts=settings.secure_cookies,
    )
    register_exception_handlers(app)
    app.include_router(api_router)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    def _api_not_found(path: str) -> None:
        raise NotFoundError(f"No API route for /api/{path}")

    # Frontend
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def _login_page() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "login.html")

    # Public unsubscribe page reached from links in emails. No login: the HMAC token proves the link.
    from fastapi import Depends, Form
    from fastapi.responses import HTMLResponse
    from sqlalchemy.orm import Session

    from app.database.session import get_db
    from app.security.deps import enforce_api_rate_limit
    from app.services import email_service

    def _page(title: str, body: str) -> HTMLResponse:
        from html import escape

        return HTMLResponse(
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{escape(title)}</title><link rel=\"stylesheet\" href=\"/assets/css/app.css\"></head>"
            f"<body class=\"auth-page\"><main class=\"card auth-card\"><h1>{escape(title)}</h1>{body}</main></body></html>"
        )

    @app.get("/unsubscribe", include_in_schema=False, dependencies=[Depends(enforce_api_rate_limit)])
    def _unsubscribe_form(e: str = "", t: str = "") -> HTMLResponse:
        from html import escape

        from app.marketing import compliance

        if not compliance.unsubscribe_valid(e.strip().lower(), t, settings.secret_key):
            return _page("Link not valid", "<p class=\"muted\">This unsubscribe link is incomplete or invalid.</p>")
        return _page("Unsubscribe", (
            f"<p class=\"muted\">Stop marketing emails to <strong>{escape(e)}</strong>?</p>"
            "<form method=\"post\" action=\"/unsubscribe\">"
            f"<input type=\"hidden\" name=\"e\" value=\"{escape(e)}\"><input type=\"hidden\" name=\"t\" value=\"{escape(t)}\">"
            "<button class=\"btn btn-primary btn-block\" type=\"submit\">Unsubscribe</button></form>"
        ))

    @app.post("/unsubscribe", include_in_schema=False, dependencies=[Depends(enforce_api_rate_limit)])
    def _unsubscribe(e: str = Form(""), t: str = Form(""), db: Session = Depends(get_db)) -> HTMLResponse:
        if not email_service.unsubscribe(db, settings, e, t):
            return _page("Link not valid", "<p class=\"muted\">This unsubscribe link is incomplete or invalid.</p>")
        db.commit()
        return _page("You're unsubscribed", "<p class=\"muted\">You will not receive further marketing emails at this address.</p>")

    @app.get("/favicon.ico", include_in_schema=False)
    def _favicon() -> RedirectResponse:
        return RedirectResponse("/assets/favicon.svg")

    return app


app = create_app()
