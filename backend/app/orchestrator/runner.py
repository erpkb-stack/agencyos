"""Where tasks execute.

The database is the queue: a task waiting to run is simply ``PENDING``. A worker *claims* a task by
moving it PENDING → ASSIGNED in a single conditional UPDATE, so two workers (threads or processes)
can never run the same task. Queued work therefore survives restarts.

* ``QueueWorker`` — polls the queue and runs up to ``TASK_WORKERS`` requests at once. Runs inside
  the web process by default, or on its own via ``python -m app.cli worker``.
* ``InlineRunner`` — runs a task immediately in the caller's thread (tests).
* ``NotifyOnlyRunner`` — used by the web process when the worker runs separately: nothing to do,
  the task is already queued in the database.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol

from sqlalchemy import case, func, inspect, select, update
from sqlalchemy.orm import Session

from app.models.ai import AgentExecution, AgentTask
from app.models.enums import ExecutionStatus, TaskPriority, TaskStatus as S
from app.orchestrator.engine import Orchestrator
from app.orchestrator.state import transition
from app.utils.time import utcnow

logger = logging.getLogger("agencyos.runner")

PRIORITY_RANK = case(
    (AgentTask.priority == TaskPriority.URGENT, 0), (AgentTask.priority == TaskPriority.HIGH, 1),
    (AgentTask.priority == TaskPriority.NORMAL, 2), else_=3,
)


class TaskRunner(Protocol):
    def submit(self, task_id: int) -> None: ...


class InlineRunner:
    """Runs the task before returning. Used by tests and the CLI."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def submit(self, task_id: int) -> None:
        self.orchestrator.run(task_id)


class NotifyOnlyRunner:
    """The task is already queued (PENDING); a separate worker process will pick it up."""

    def submit(self, task_id: int) -> None:
        return None


def claim_next(db: Session, limit: int = 1) -> list[int]:
    """Atomically claim up to ``limit`` queued tasks, most urgent first, oldest first."""
    candidates = list(db.scalars(
        select(AgentTask.id).where(AgentTask.status == S.PENDING)
        .order_by(PRIORITY_RANK, AgentTask.id).limit(limit * 3)
    ))
    claimed: list[int] = []
    for task_id in candidates:
        if len(claimed) >= limit:
            break
        result = db.execute(
            update(AgentTask).where(AgentTask.id == task_id, AgentTask.status == S.PENDING)
            .values(status=S.ASSIGNED, updated_at=utcnow())
        )
        db.commit()
        if result.rowcount == 1:
            claimed.append(task_id)
    return claimed


def queue_depth(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status == S.PENDING)) or 0


class QueueWorker:
    """Polls the database queue; ``submit`` just wakes it up early."""

    def __init__(self, orchestrator: Orchestrator, session_factory: Callable[[], Session],
                 workers: int = 2, poll_seconds: float = 2.0) -> None:
        self.orchestrator = orchestrator
        self.session_factory = session_factory
        self.workers = workers
        self.poll_seconds = poll_seconds
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agencyos-task")
        self._running: set[Future] = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="agencyos-queue", daemon=True)

    def start(self) -> QueueWorker:
        self._thread.start()
        return self

    def submit(self, task_id: int) -> None:
        self._wake.set()

    def _free_slots(self) -> int:
        with self._lock:
            self._running = {f for f in self._running if not f.done()}
            return self.workers - len(self._running)

    def _run(self, task_id: int) -> None:
        try:
            self.orchestrator.run(task_id)
        except Exception:  # noqa: BLE001 - keep the worker alive whatever a task does
            logger.exception("Task %s crashed", task_id)
        finally:
            self._wake.set()  # a slot freed up: look for more work

    def run_once(self) -> int:
        """Claim and start as many queued tasks as there are free slots. Returns how many started."""
        slots = self._free_slots()
        if slots <= 0:
            return 0
        try:
            with self.session_factory() as db:
                if not inspect(db.get_bind()).has_table("agent_tasks"):
                    return 0
                ids = claim_next(db, slots)
        except Exception:  # noqa: BLE001 - e.g. database briefly unavailable; retry on next poll
            logger.exception("Could not read the task queue")
            return 0
        for task_id in ids:
            future = self._pool.submit(self._run, task_id)
            with self._lock:
                self._running.add(future)
        return len(ids)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self.run_once()
            if started:
                continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until no task is running and nothing is queued (used by tests and the CLI)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.session_factory() as db:
                depth = queue_depth(db)
            if depth == 0 and self._free_slots() == self.workers:
                return True
            self._wake.set()
            time.sleep(0.05)
        return False

    def shutdown(self, wait: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


def recover_interrupted(db: Session) -> dict[str, int]:
    """After a restart: queued tasks stay queued; requests that were running are re-queued once.

    * PENDING tasks: untouched (the worker picks them up).
    * Running top-level requests: re-queued if they have an attempt left, otherwise FAILED.
    * Running sub-tasks: FAILED (their request is re-planned when it is re-queued).
    """
    if not inspect(db.get_bind()).has_table("agent_tasks"):
        return {"requeued": 0, "failed": 0}
    requeued = failed = 0
    stuck = list(db.scalars(select(AgentTask).where(AgentTask.status.in_([S.ASSIGNED, S.RUNNING]))))
    for task in stuck:
        if task.status == S.ASSIGNED:
            transition(task, S.RUNNING)
        transition(task, S.FAILED)
        if task.parent_task_id is None and task.attempts < task.max_attempts:
            transition(task, S.PENDING)
            task.error = None
            task.output = {**(task.output or {}), "notes": ["Interrupted by a server restart and re-queued automatically."]}
            requeued += 1
        else:
            task.error = "Interrupted by a server restart; use Retry to run it again"
            failed += 1
    for execution in db.scalars(select(AgentExecution).where(AgentExecution.status == ExecutionStatus.RUNNING)):
        execution.status = ExecutionStatus.FAILED
        execution.error = "Interrupted by a server restart"
        execution.finished_at = utcnow()
    db.commit()
    return {"requeued": requeued, "failed": failed}
