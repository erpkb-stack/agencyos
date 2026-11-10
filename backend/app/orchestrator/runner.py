"""Where tasks execute. Callers only ``submit(task_id)``; swapping in a worker queue later means
adding another runner, not changing the API or the orchestrator."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.ai import AgentExecution, AgentTask
from app.models.enums import ExecutionStatus, TaskStatus as S
from app.orchestrator.engine import Orchestrator
from app.orchestrator.state import transition
from app.utils.time import utcnow

logger = logging.getLogger("agencyos.runner")


class TaskRunner(Protocol):
    def submit(self, task_id: int) -> None: ...


class InlineRunner:
    """Runs the task before returning. Used by tests and the CLI."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def submit(self, task_id: int) -> None:
        self.orchestrator.run(task_id)


class ThreadRunner:
    """In-process background execution for local development (one worker keeps SQLite happy)."""

    def __init__(self, orchestrator: Orchestrator, workers: int = 1) -> None:
        self.orchestrator = orchestrator
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agencyos-task")

    def submit(self, task_id: int) -> None:
        future = self._pool.submit(self.orchestrator.run, task_id)
        future.add_done_callback(
            lambda f: f.exception() and logger.error("Task %s crashed: %s", task_id, f.exception())
        )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def recover_interrupted(db: Session) -> int:
    """Fail tasks left mid-run by a restart; they would otherwise look busy forever."""
    if not inspect(db.get_bind()).has_table("agent_tasks"):
        return 0
    stuck = list(db.scalars(select(AgentTask).where(AgentTask.status.in_([S.PENDING, S.ASSIGNED, S.RUNNING]))))
    for task in stuck:
        if task.status == S.PENDING:
            transition(task, S.ASSIGNED)
        if task.status == S.ASSIGNED:
            transition(task, S.RUNNING)
        transition(task, S.FAILED)
        task.error = "Interrupted by a server restart; use Retry to run it again"
    for execution in db.scalars(select(AgentExecution).where(AgentExecution.status == ExecutionStatus.RUNNING)):
        execution.status = ExecutionStatus.FAILED
        execution.error = "Interrupted by a server restart"
        execution.finished_at = utcnow()
    db.commit()
    return len(stuck)
