"""The task state machine. Every status change goes through ``transition``."""

from __future__ import annotations

from app.models.ai import AgentTask
from app.models.enums import TaskStatus as S
from app.utils.errors import ConflictError
from app.utils.time import utcnow

TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.ASSIGNED, S.CANCELLED}),
    S.ASSIGNED: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.COMPLETED, S.FAILED, S.AWAITING_APPROVAL, S.CANCELLED}),
    S.AWAITING_APPROVAL: frozenset({S.COMPLETED, S.CANCELLED}),
    S.FAILED: frozenset({S.PENDING}),  # retry
    S.COMPLETED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL = frozenset({S.COMPLETED, S.CANCELLED})
ACTIVE = frozenset({S.PENDING, S.ASSIGNED, S.RUNNING})


class InvalidTransition(ConflictError):
    code = "invalid_transition"


def can_transition(current: S, new: S) -> bool:
    return new in TRANSITIONS[current]


def transition(task: AgentTask, new: S) -> None:
    current = S(task.status)
    if not can_transition(current, new):
        raise InvalidTransition(f"Task {task.id} cannot move from {current.value} to {new.value}")
    task.status = new
    now = utcnow()
    if new == S.RUNNING:
        task.started_at = now
    if new in TERMINAL or new == S.FAILED:
        task.completed_at = now
    if new == S.PENDING:
        task.completed_at = None
        task.error = None
