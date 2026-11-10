"""Orchestrator wiring. ``get_task_runner`` is a FastAPI dependency so tests can swap in an
``InlineRunner`` with a fake provider."""

from __future__ import annotations

from threading import Lock

from app.config.settings import get_settings
from app.database.session import SessionLocal
from app.orchestrator.engine import Orchestrator
from app.orchestrator.runner import InlineRunner, NotifyOnlyRunner, QueueWorker, TaskRunner
from app.providers import default_provider_factory

_runner: TaskRunner | None = None
_email_sender = None
_lock = Lock()


def build_worker() -> QueueWorker:
    settings = get_settings()
    return QueueWorker(Orchestrator(SessionLocal, default_provider_factory, settings), SessionLocal,
                       workers=settings.task_workers, poll_seconds=settings.worker_poll_seconds)


def get_task_runner() -> TaskRunner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = build_worker().start() if get_settings().run_worker_in_process else NotifyOnlyRunner()
        return _runner


def build_email_sender():
    from app.services.email_sending import EmailSender

    return EmailSender(SessionLocal, get_settings())


def start_runner() -> None:
    """Start the in-process worker at boot so queued work resumes without waiting for a request.
    The email sender runs wherever the worker runs (here, or in `python -m app.cli worker`)."""
    global _email_sender
    get_task_runner()
    with _lock:
        if get_settings().run_worker_in_process and _email_sender is None:
            _email_sender = build_email_sender().start()


def shutdown_runner() -> None:
    global _runner
    with _lock:
        if isinstance(_runner, QueueWorker):
            _runner.shutdown()
        _runner = None
        global _email_sender
        if _email_sender is not None:
            _email_sender.shutdown()
            _email_sender = None


__all__ = ["build_email_sender", "Orchestrator", "InlineRunner", "QueueWorker", "TaskRunner", "get_task_runner", "start_runner",
           "shutdown_runner"]
