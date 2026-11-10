from typing import Any

from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session

from app.config.settings import APP_VERSION, Settings, get_settings
from app.database.session import get_db
from app.orchestrator import budget
from app.orchestrator.runner import queue_depth
from app.models.identity import User
from app.security.deps import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/system")
def system_info(
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Non-secret runtime configuration. Key values are never returned, only whether set."""
    return {
        "app_name": settings.app_name,
        "version": APP_VERSION,
        "environment": settings.app_env.value,
        "database_backend": settings.database_backend,
        "llm": {
            "provider": settings.llm_provider.value,
            "openai_configured": bool(settings.openai_api_key),
            "openai_model": settings.openai_model,
            "anthropic_configured": bool(settings.anthropic_api_key),
            "anthropic_model": settings.anthropic_model,
            "anthropic_effort": settings.anthropic_effort,
            "timeout_seconds": settings.llm_timeout_seconds,
        },
        "orchestrator": {
            "max_subtasks": settings.orchestrator_max_subtasks,
            "max_depth": settings.orchestrator_max_depth,
            "parallel_agents": settings.orchestrator_parallel_agents,
            "task_workers": settings.task_workers,
            "worker_in_process": settings.run_worker_in_process,
            "queued": queue_depth(db),
            **budget.summary(db, settings),
        },
        "demo_login_enabled": settings.demo_login_enabled,
        "session_ttl_hours": settings.session_ttl_hours,
        "external_sending_enabled": False,
        "api_docs_enabled": settings.api_docs_enabled,
        "warnings": settings.production_warnings(),
    }
