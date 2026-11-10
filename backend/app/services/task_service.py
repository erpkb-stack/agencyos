"""Conversations, tasks and approval decisions (the user-facing side of the orchestrator)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import approvals as approval_gate
from app.agents.registry import CEO_KEY
from app.models.ai import Agent, AgentExecution, AgentTask, Conversation, Message
from app.models.approvals import Approval
from app.models.enums import ActorType, ApprovalStatus, MessageRole, TaskPriority, TaskStatus as S, UserRole
from app.models.identity import User
from app.orchestrator.state import TERMINAL, transition
from app.services import agent_service, audit_service
from app.utils.errors import AppError, NotFoundError, PermissionDeniedError
from app.utils.time import utcnow

# -- conversations -----------------------------------------------------------------------------


def _own_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.deleted_at is not None or conversation.user_id != user.id:
        raise NotFoundError("Conversation not found")
    return conversation


def list_conversations(db: Session, user: User) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [
        {"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at,
         "message_count": n}
        for c, n in rows
    ]


def get_conversation(db: Session, user: User, conversation_id: int) -> dict[str, Any]:
    conversation = _own_conversation(db, user, conversation_id)
    messages = list(db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    ))
    task_ids = {m.task_id for m in messages if m.task_id}
    tasks = {
        t.id: t for t in db.scalars(select(AgentTask).where(AgentTask.id.in_(task_ids)))
    } if task_ids else {}
    return {
        "id": conversation.id, "title": conversation.title,
        "created_at": conversation.created_at, "updated_at": conversation.updated_at,
        "messages": [
            {"id": m.id, "role": m.role.value, "content": m.content, "task_id": m.task_id,
             "meta": m.meta, "created_at": m.created_at,
             "task": task_tree(db, tasks[m.task_id]) if m.task_id in tasks and m.role == MessageRole.USER else None}
            for m in messages
        ],
    }


def rename_conversation(db: Session, user: User, conversation_id: int, title: str) -> Conversation:
    conversation = _own_conversation(db, user, conversation_id)
    conversation.title = title
    db.commit()
    return conversation


def delete_conversation(db: Session, user: User, conversation_id: int) -> None:
    conversation = _own_conversation(db, user, conversation_id)
    conversation.deleted_at = utcnow()
    db.commit()


def ceo_agent(db: Session) -> Agent:
    agent_service.ensure_seeded(db)
    agent = agent_service.get_by_key(db, CEO_KEY)
    if agent is None:  # pragma: no cover - ensure_seeded creates it
        raise AppError("The AI CEO agent is missing; run `python -m app.cli init-db`")
    return agent


def send_ceo_message(db: Session, user: User, content: str, conversation_id: int | None) -> dict[str, Any]:
    """Store the Founder's message and create the CEO root task (the runner executes it)."""
    ceo = ceo_agent(db)
    if conversation_id is None:
        conversation = Conversation(user_id=user.id, title=" ".join(content.split())[:80] or "New conversation")
        db.add(conversation)
        db.flush()
    else:
        conversation = _own_conversation(db, user, conversation_id)
        conversation.updated_at = utcnow()
    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db.add(message)
    db.flush()
    task = AgentTask(
        conversation_id=conversation.id, agent_id=ceo.id, created_by_user_id=user.id,
        title=" ".join(content.split())[:200], task_type="ceo_request", priority=TaskPriority.NORMAL,
        status=S.PENDING, depth=0, input={"instructions": content, "message_id": message.id},
    )
    db.add(task)
    db.flush()
    message.task_id = task.id
    audit_service.record(db, "ceo.request", user=user, actor_type=ActorType.USER,
                         entity_type="agent_task", entity_id=task.id,
                         summary=f"Asked the AI CEO: {task.title[:120]}")
    db.commit()
    return {"conversation_id": conversation.id, "message_id": message.id, "task_id": task.id}


# -- tasks -------------------------------------------------------------------------------------


def get_task(db: Session, task_id: int) -> AgentTask:
    task = db.get(AgentTask, task_id)
    if task is None:
        raise NotFoundError("Task not found")
    return task


def create_task(db: Session, user: User, *, agent_id: int, title: str, instructions: str,
                priority: TaskPriority, context: dict[str, Any] | None = None,
                task_type: str = "direct", project_id: int | None = None,
                link: dict[str, Any] | None = None) -> AgentTask:
    agent = agent_service.get_agent(db, agent_id)
    if not agent.is_active:
        raise AppError("This agent is inactive; activate it first")
    task = AgentTask(
        agent_id=agent.id, created_by_user_id=user.id, title=title, task_type=task_type, project_id=project_id,
        priority=priority, status=S.PENDING, depth=0,
        input={"instructions": instructions, "context": context or {}, **({"link": link} if link else {})},
    )
    db.add(task)
    db.flush()
    audit_service.record(db, "task.created", user=user, actor_type=ActorType.USER,
                         entity_type="agent_task", entity_id=task.id,
                         summary=f"Assigned '{title[:120]}' to {agent.name}")
    db.commit()
    return task


def _expire_pending_approvals(db: Session, task: AgentTask, reason: str) -> None:
    for approval in db.scalars(select(Approval).where(Approval.task_id == task.id,
                                                      Approval.status == ApprovalStatus.PENDING)):
        approval.status = ApprovalStatus.EXPIRED
        approval.comments = reason
        approval.decided_at = utcnow()


def cancel_task(db: Session, user: User, task: AgentTask) -> AgentTask:
    if task.status in TERMINAL:
        raise AppError(f"Task is already {task.status.value.lower()}")
    if task.status == S.FAILED:
        raise AppError("Failed tasks cannot be cancelled; retry them or leave them")
    stack = [task]
    while stack:
        current = stack.pop()
        if current.status not in TERMINAL and current.status != S.FAILED:
            transition(current, S.CANCELLED)
            _expire_pending_approvals(db, current, "Task was cancelled")
        stack.extend(db.scalars(select(AgentTask).where(AgentTask.parent_task_id == current.id)))
    audit_service.record(db, "task.cancelled", user=user, actor_type=ActorType.USER,
                         entity_type="agent_task", entity_id=task.id, summary=f"Cancelled '{task.title[:120]}'")
    db.commit()
    return task


def retry_task(db: Session, user: User, task: AgentTask) -> AgentTask:
    if task.status != S.FAILED:
        raise AppError("Only failed tasks can be retried")
    if task.agent is None or not task.agent.is_active:
        raise AppError("The assigned agent is missing or inactive")
    transition(task, S.PENDING)
    task.attempts = 0  # a manual retry gets a fresh, still bounded, attempt budget
    audit_service.record(db, "task.retried", user=user, actor_type=ActorType.USER,
                         entity_type="agent_task", entity_id=task.id, summary=f"Retried '{task.title[:120]}'")
    db.commit()
    return task


def list_tasks(db: Session, *, status: S | None, agent_id: int | None, root_only: bool, q: str | None,
               offset: int, limit: int) -> tuple[list[AgentTask], int]:
    query = select(AgentTask)
    if status:
        query = query.where(AgentTask.status == status)
    if agent_id:
        query = query.where(AgentTask.agent_id == agent_id)
    if root_only:
        query = query.where(AgentTask.parent_task_id.is_(None))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(AgentTask.title.ilike(like), AgentTask.task_type.ilike(like)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(AgentTask.id.desc()).offset(offset).limit(limit)))
    return rows, total


def phase_of(task: AgentTask) -> str:
    """User-facing stage: planning is a RUNNING CEO task that has not delegated yet."""
    if task.status == S.RUNNING and (task.output or {}).get("phase") == "planning":
        return "PLANNING"
    return task.status.value


def task_summary(task: AgentTask) -> dict[str, Any]:
    result = (task.output or {}).get("result") or (task.output or {}).get("final") or {}
    return {
        "id": task.id, "parent_task_id": task.parent_task_id, "conversation_id": task.conversation_id,
        "title": task.title, "task_type": task.task_type, "priority": task.priority.value,
        "status": task.status.value, "phase": phase_of(task),
        "agent": {"id": task.agent.id, "name": task.agent.name, "key": task.agent.key} if task.agent else None,
        "attempts": task.attempts, "max_attempts": task.max_attempts, "depth": task.depth,
        "project_id": task.project_id,
        "requires_approval": task.requires_approval, "error": task.error,
        "summary": result.get("summary"),
        "created_at": task.created_at, "started_at": task.started_at, "completed_at": task.completed_at,
    }


def task_tree(db: Session, task: AgentTask) -> dict[str, Any]:
    children = list(db.scalars(select(AgentTask).where(AgentTask.parent_task_id == task.id).order_by(AgentTask.id)))
    return {**task_summary(task), "children": [task_tree(db, c) for c in children]}


def execution_out(e: AgentExecution) -> dict[str, Any]:
    return {
        "id": e.id, "task_id": e.task_id, "agent": e.agent.name if e.agent else None,
        "status": e.status.value, "provider": e.provider, "model": e.model, "error": e.error,
        "prompt_tokens": e.prompt_tokens, "completion_tokens": e.completion_tokens,
        "estimated_cost": f"{e.estimated_cost:.6f}" if e.estimated_cost is not None else None,
        "duration_ms": e.duration_ms, "started_at": e.started_at, "finished_at": e.finished_at,
    }


def task_detail(db: Session, task: AgentTask) -> dict[str, Any]:
    executions = list(db.scalars(select(AgentExecution).where(AgentExecution.task_id == task.id)
                                 .order_by(AgentExecution.id)))
    approvals = list(db.scalars(select(Approval).where(Approval.task_id == task.id).order_by(Approval.id)))
    return {
        **task_tree(db, task),
        "input": task.input, "output": task.output,
        "executions": [execution_out(e) for e in executions],
        "approvals": [approval_out(db, a) for a in approvals],
    }


# -- approvals ---------------------------------------------------------------------------------


def expire_overdue(db: Session) -> None:
    now = utcnow()
    overdue = list(db.scalars(select(Approval).where(Approval.status == ApprovalStatus.PENDING,
                                                     Approval.expires_at.is_not(None),
                                                     Approval.expires_at < now)))
    for approval in overdue:
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        approval.comments = "Expired without a decision"
        _settle_task(db, approval)
        approval_gate.settle(db, approval)
    if overdue:
        db.commit()


def approval_out(db: Session, a: Approval) -> dict[str, Any]:
    agent = db.get(Agent, a.requested_by_agent_id) if a.requested_by_agent_id else None
    decider = db.get(User, a.decided_by_user_id) if a.decided_by_user_id else None
    requester = db.get(User, a.requested_by_user_id) if a.requested_by_user_id else None
    return {
        "id": a.id, "action_type": a.action_type.value, "title": a.title, "status": a.status.value,
        "payload": a.payload, "task_id": a.task_id, "client_id": a.client_id,
        "requested_by_agent": agent.name if agent else None,
        "requested_by_user": requester.full_name if requester else None,
        "entity_type": (a.payload or {}).get("entity_type"), "entity_id": (a.payload or {}).get("entity_id"),
        "decided_by": decider.full_name if decider else None,
        "decided_at": a.decided_at, "comments": a.comments,
        "expires_at": a.expires_at, "created_at": a.created_at,
    }


def list_approvals(db: Session, status: ApprovalStatus | None, offset: int, limit: int) -> tuple[list[Approval], int]:
    expire_overdue(db)
    query = select(Approval)
    if status:
        query = query.where(Approval.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Approval.id.desc()).offset(offset).limit(limit)))
    return rows, total


def _settle_task(db: Session, approval: Approval) -> None:
    """Once no approval on a task is pending, the task completes (any approved) or is cancelled."""
    if approval.task_id is None:
        return
    task = db.get(AgentTask, approval.task_id)
    if task is None or task.status != S.AWAITING_APPROVAL:
        return
    siblings = list(db.scalars(select(Approval).where(Approval.task_id == task.id)))
    if any(s.status == ApprovalStatus.PENDING and s.id != approval.id for s in siblings):
        return
    if any(s.status == ApprovalStatus.APPROVED for s in siblings):
        transition(task, S.COMPLETED)
    else:
        transition(task, S.CANCELLED)


def decide_approval(db: Session, user: User, approval_id: int, decision: ApprovalStatus,
                    comments: str | None) -> Approval:
    if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise AppError("Decision must be APPROVED or REJECTED")
    expire_overdue(db)
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise NotFoundError("Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise AppError(f"This approval is already {approval.status.value.lower()}")
    # Four-eyes rule for admins; the Founder owns the business and may approve their own requests.
    if (approval.requested_by_user_id is not None and approval.requested_by_user_id == user.id
            and user.role != UserRole.FOUNDER):
        raise PermissionDeniedError("You cannot approve your own request")
    approval.status = decision
    approval.decided_by_user_id = user.id
    approval.decided_at = utcnow()
    approval.comments = comments
    _settle_task(db, approval)
    approval_gate.settle(db, approval)
    audit_service.record(
        db, f"approval.{decision.value.lower()}", user=user, actor_type=ActorType.USER,
        entity_type="approval", entity_id=approval.id,
        summary=f"{decision.value.title()} '{approval.title[:120]}'. No integration is connected, so nothing was executed.",
    )
    db.commit()
    return approval
