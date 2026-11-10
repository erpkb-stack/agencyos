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
from app.orchestrator import shutdown_runner
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
        if added or recovered:
            logger.info(f"Startup: {added} built-in agents added, {recovered} interrupted tasks marked failed")
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
        _startup_housekeeping(logger)
        yield
        shutdown_runner()
        logger.info("AgencyOS stopped")

    app = FastAPI(
        title="AgencyOS API",
        version=APP_VERSION,
        description="Internal AI agency operating system. All endpoints under /api/v1.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)
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

    @app.get("/favicon.ico", include_in_schema=False)
    def _favicon() -> RedirectResponse:
        return RedirectResponse("/assets/favicon.svg")

    return app


app = create_app()
