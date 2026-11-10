"""Orchestrator wiring. ``get_task_runner`` is a FastAPI dependency so tests can swap in an
``InlineRunner`` with a fake provider."""

from __future__ import annotations

from threading import Lock

from app.config.settings import get_settings
from app.database.session import SessionLocal
from app.orchestrator.engine import Orchestrator
from app.orchestrator.runner import InlineRunner, TaskRunner, ThreadRunner
from app.providers import default_provider_factory

_runner: ThreadRunner | None = None
_lock = Lock()


def get_task_runner() -> TaskRunner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = ThreadRunner(Orchestrator(SessionLocal, default_provider_factory, get_settings()))
        return _runner


def shutdown_runner() -> None:
    global _runner
    with _lock:
        if _runner is not None:
            _runner.shutdown()
            _runner = None


__all__ = ["Orchestrator", "InlineRunner", "TaskRunner", "ThreadRunner", "get_task_runner", "shutdown_runner"]
