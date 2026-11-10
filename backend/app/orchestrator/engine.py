"""The orchestrator: runs tasks, delegates, retries, records executions and raises approvals.

It works with task IDs and its own database session, so the same code can later run in a
separate worker process. All permission, state and approval decisions are made here in Python.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import (
    AgentError, AgentResult, CEOAgent, ExecutionRecorder, agent_class_for,
)
from app.agents.registry import CEO_KEY, SPECS_BY_KEY, TOOL_BUSINESS_DATA, TOOL_PROPOSE
from app.agents.schemas import CEOPlan, CEOSynthesis, SpecialistOutput
from app.config.settings import Settings
from app.models.ai import Agent, AgentExecution, AgentTask, Conversation, Message
from app.models.approvals import Approval
from app.models.enums import (
    ActorType, ApprovalStatus, ExecutionStatus, MessageRole, TaskPriority, TaskStatus as S,
)
from app.orchestrator.policy import validate_plan
from app.orchestrator.state import transition
from app.providers import ProviderFactory
from app.providers.base import ChatMessage, LLMResponse, ProviderError
from app.services import agent_service, audit_service, dashboard_service
from app.utils.time import utcnow

logger = logging.getLogger("agencyos.orchestrator")

APPROVAL_TTL = timedelta(days=7)
DRAFT_ONLY_TASK_TYPES = {"campaign_generate", "campaign_analyze", "email_generate", "funnel_generate"}
HISTORY_MESSAGES = 10


class DbRecorder:
    """Persists one AgentExecution row per provider call."""

    def __init__(self, db: Session, task: AgentTask, agent: Agent) -> None:
        self.db, self.task, self.agent = db, task, agent

    def start(self, *, provider: str, model: str, request: dict[str, Any]) -> int:
        execution = AgentExecution(
            task_id=self.task.id, agent_id=self.agent.id, status=ExecutionStatus.RUNNING,
            provider=provider, model=model, input=request,
        )
        self.db.add(execution)
        self.db.commit()
        return execution.id

    def finish(self, execution_id: int, *, response: LLMResponse | None, output: dict[str, Any] | None,
               error: str | None, duration_ms: int) -> None:
        execution = self.db.get(AgentExecution, execution_id)
        execution.status = ExecutionStatus.FAILED if error else ExecutionStatus.SUCCEEDED
        execution.output = output
        execution.error = error
        execution.duration_ms = duration_ms
        execution.finished_at = utcnow()
        if response is not None:
            execution.model = response.model
            execution.prompt_tokens = response.prompt_tokens
            execution.completion_tokens = response.completion_tokens
            execution.estimated_cost = response.estimated_cost
        self.agent.last_execution_at = execution.finished_at
        self.db.commit()


class Orchestrator:
    def __init__(self, session_factory: Callable[[], Session], provider_factory: ProviderFactory,
                 settings: Settings) -> None:
        self.session_factory = session_factory
        self.provider_factory = provider_factory
        self.settings = settings

    # -- entry point -----------------------------------------------------------------------

    def run(self, task_id: int) -> None:
        with self.session_factory() as db:
            task = db.get(AgentTask, task_id)
            if task is None or task.status != S.PENDING:
                return
            try:
                if task.agent is not None and task.agent.key == CEO_KEY:
                    self._run_ceo(db, task)
                else:
                    self._run_specialist(db, task)
            except Exception:  # noqa: BLE001 - last-resort guard so a task never hangs in RUNNING
                logger.exception("Orchestrator crashed on task %s", task_id)
                db.rollback()
                task = db.get(AgentTask, task_id)
                if task is not None and task.status in (S.ASSIGNED, S.RUNNING):
                    if task.status == S.ASSIGNED:
                        transition(task, S.RUNNING)
                    transition(task, S.FAILED)
                    task.error = "Internal orchestrator error (details are in the server log)"
                    db.commit()

    # -- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _cancelled(db: Session, task: AgentTask) -> bool:
        db.refresh(task)
        return task.status == S.CANCELLED

    def _build_agent(self, db: Session, agent: Agent, **extra: Any):
        config = agent_service.to_config(agent, self.settings)
        provider = self.provider_factory(agent.model_provider)
        return agent_class_for(agent.key)(config, provider, self.settings.llm_max_tokens, **extra)

    def _business_snapshot(self, db: Session) -> dict[str, Any]:
        from app.services import project_service

        return {"generated_at": utcnow().isoformat(), "kpis": dashboard_service.kpis(db),
                "open_projects": project_service.active_projects_snapshot(db)}

    def _attempt(self, db: Session, task: AgentTask, call: Callable[[], AgentResult]) -> AgentResult | None:
        """Run ``call`` with bounded retries. Returns None if the task failed or was cancelled."""
        if task.status == S.PENDING:
            transition(task, S.ASSIGNED)
            db.commit()
        while True:
            if self._cancelled(db, task):
                return None
            transition(task, S.RUNNING)
            task.attempts += 1
            db.commit()
            try:
                result = call()
            except (AgentError, ProviderError) as exc:
                if self._cancelled(db, task):
                    return None
                transition(task, S.FAILED)
                task.error = exc.message
                db.commit()
                if exc.retryable and task.attempts < task.max_attempts:
                    transition(task, S.PENDING)
                    transition(task, S.ASSIGNED)
                    db.commit()
                    continue
                audit_service.record(
                    db, "task.failed", actor_type=ActorType.AGENT, actor_label=task.agent.name,
                    entity_type="agent_task", entity_id=task.id,
                    summary=f"{task.title} failed: {exc.message}",
                )
                db.commit()
                return None
            if self._cancelled(db, task):
                return None
            return result

    def _request_approvals(self, db: Session, task: AgentTask, output: SpecialistOutput) -> int:
        now = utcnow()
        for action in output.proposed_actions:
            approval = Approval(
                action_type=action.action_type, title=action.title,
                requested_by_agent_id=task.agent_id, task_id=task.id,
                payload={"details": action.details, "agent": task.agent.name, "task_title": task.title},
                status=ApprovalStatus.PENDING, expires_at=now + APPROVAL_TTL,
            )
            db.add(approval)
            db.flush()
            audit_service.record(
                db, "approval.requested", actor_type=ActorType.AGENT, actor_label=task.agent.name,
                entity_type="approval", entity_id=approval.id,
                summary=f"{task.agent.name} requested approval: {action.title}",
            )
        return len(output.proposed_actions)

    # -- specialist ------------------------------------------------------------------------

    def _run_specialist(self, db: Session, task: AgentTask) -> None:
        agent = task.agent
        if agent is None or not agent.is_active:
            if task.status == S.PENDING:
                transition(task, S.ASSIGNED)
            transition(task, S.RUNNING)
            transition(task, S.FAILED)
            task.error = "The assigned agent is missing or inactive"
            db.commit()
            return

        def call() -> AgentResult:
            worker = self._build_agent(db, agent)
            stored = task.input or {}
            # Only instructions and context reach the agent; other keys (e.g. record links) stay server-side.
            context = dict(stored.get("context") or {})
            if TOOL_BUSINESS_DATA in agent.allowed_tools:
                context["business_snapshot"] = self._business_snapshot(db)
            task_input = {"instructions": stored.get("instructions", ""), "context": context}
            return worker.execute(task_input, DbRecorder(db, task, agent))

        result = self._attempt(db, task, call)
        if result is None:
            return
        output: SpecialistOutput = result.output  # type: ignore[assignment]
        task.output = {
            "result": output.model_dump(mode="json"),
            "notes": result.notes,
            "provider": result.response.provider,
            "model": result.response.model,
        }
        notes = list(result.notes)
        if output.proposed_actions and task.task_type in DRAFT_ONLY_TASK_TYPES:
            # Drafting for a campaign/funnel: external actions are requested from that record's page.
            notes.append(f"{len(output.proposed_actions)} proposed action(s) not queued: approval for this "
                         "work is requested from the campaign, email or funnel page.")
            task.output = {**task.output, "notes": notes}
            transition(task, S.COMPLETED)
        elif output.proposed_actions and TOOL_PROPOSE in agent.allowed_tools:
            self._request_approvals(db, task, output)
            task.requires_approval = True
            transition(task, S.AWAITING_APPROVAL)
        else:
            transition(task, S.COMPLETED)
        db.commit()

    # -- CEO -------------------------------------------------------------------------------

    def _history(self, db: Session, root: AgentTask) -> list[ChatMessage]:
        if root.conversation_id is None:
            return []
        trigger_id = (root.input or {}).get("message_id")
        query = select(Message).where(
            Message.conversation_id == root.conversation_id,
            Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
        )
        if trigger_id:
            query = query.where(Message.id < trigger_id)
        rows = list(db.scalars(query.order_by(Message.id.desc()).limit(HISTORY_MESSAGES)))
        history = [ChatMessage(m.role.value, m.content) for m in reversed(rows)]
        # Vendors expect the conversation to start with a user turn.
        while history and history[0].role != "user":
            history.pop(0)
        return history

    def _post_message(self, db: Session, root: AgentTask, content: str, meta: dict[str, Any]) -> None:
        if root.conversation_id is None:
            return
        db.add(Message(conversation_id=root.conversation_id, role=MessageRole.ASSISTANT,
                       content=content, task_id=root.id, meta=meta))
        conversation = db.get(Conversation, root.conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()

    def _run_ceo(self, db: Session, root: AgentTask) -> None:
        ceo_agent = root.agent
        request_text = (root.input or {}).get("instructions", "")
        specialists = agent_service.active_specialists(db)
        by_key = {a.key: a for a in specialists}
        roster = [
            {"key": a.key, "name": a.name, "description": a.description,
             "keywords": list(SPECS_BY_KEY[a.key].keywords) if a.key in SPECS_BY_KEY else []}
            for a in specialists
        ]
        history = self._history(db, root)
        root.output = {"phase": "planning"}
        db.commit()

        holder: dict[str, CEOAgent] = {}

        def plan_call() -> AgentResult:
            ceo = self._build_agent(db, ceo_agent, available_agents=roster)
            holder["ceo"] = ceo
            task_input = {"instructions": request_text, "context": (root.input or {}).get("context") or {}}
            return ceo.execute(task_input, DbRecorder(db, root, ceo_agent), history)

        planned = self._attempt(db, root, plan_call)
        if planned is None:
            if root.status == S.FAILED:
                self._post_message(db, root, f"I could not plan this request: {root.error}",
                                   {"task_id": root.id, "status": root.status.value})
                db.commit()
            return
        plan: CEOPlan = planned.output  # type: ignore[assignment]
        ceo = holder["ceo"]

        decision = validate_plan(
            plan, set(by_key), chain_keys=[CEO_KEY], depth=root.depth + 1,
            max_depth=self.settings.orchestrator_max_depth,
            max_subtasks=self.settings.orchestrator_max_subtasks,
        )
        root.task_type = plan.task_type
        root.output = {
            "phase": "delegating",
            "plan": plan.model_dump(mode="json"),
            "rejected_subtasks": decision.rejected,
            "notes": planned.notes,
        }
        children: list[AgentTask] = []
        for sub in decision.accepted:
            child = AgentTask(
                parent_task_id=root.id, conversation_id=root.conversation_id,
                agent_id=by_key[sub.agent_key].id, created_by_user_id=root.created_by_user_id,
                title=sub.title, task_type=plan.task_type, priority=TaskPriority(sub.priority),
                status=S.PENDING, depth=root.depth + 1,
                input={"instructions": sub.instructions, "context": {"founder_request": request_text}},
            )
            db.add(child)
            children.append(child)
        db.flush()
        for child in children:
            transition(child, S.ASSIGNED)
        audit_service.record(
            db, "ceo.plan_created", actor_type=ActorType.AGENT, actor_label=ceo_agent.name,
            entity_type="agent_task", entity_id=root.id,
            summary=(f"Delegated '{root.title}' to " + ", ".join(by_key[s.agent_key].name for s in decision.accepted))
            if children else f"Answered '{root.title}' directly",
            details={"subtasks": [c.id for c in children], "rejected": decision.rejected},
        )
        db.commit()

        if not children:
            root.output = {**root.output, "phase": "done", "final": {"summary": plan.reply, "next_steps": []}}
            transition(root, S.COMPLETED)
            self._post_message(db, root, plan.reply, {"task_id": root.id, "status": "COMPLETED", "subtasks": []})
            db.commit()
            return

        for child in children:
            if self._cancelled(db, root):
                return
            self._run_specialist(db, child)

        if self._cancelled(db, root):
            return
        for child in children:
            db.refresh(child)
        root.output = {**root.output, "phase": "synthesizing"}
        db.commit()

        results = []
        for child in children:
            out = (child.output or {}).get("result") or {}
            results.append({
                "agent": child.agent.name, "status": child.status.value,
                "summary": out.get("summary"), "error": child.error,
                "deliverables": [d.get("title") for d in out.get("deliverables", [])],
                "awaiting_approval": child.status == S.AWAITING_APPROVAL,
            })

        try:
            synth = ceo.synthesize(request_text, results, DbRecorder(db, root, ceo_agent))
            final: dict[str, Any] = synth.output.model_dump(mode="json")
        except AgentError as exc:
            final = CEOSynthesis(
                summary="Automatic summary (the CEO summary step failed: " + exc.message + "):\n" + "\n".join(
                    f"• {r['agent']}: {r['summary'] or r['status'].lower()}" for r in results),
                next_steps=["Open the task details to read each deliverable."],
            ).model_dump(mode="json")

        if self._cancelled(db, root):
            return
        failed = all(c.status in (S.FAILED, S.CANCELLED) for c in children)
        root.output = {**root.output, "phase": "done", "final": final}
        if failed:
            transition(root, S.FAILED)
            root.error = "All delegated sub-tasks failed"
        else:
            transition(root, S.COMPLETED)
        content = final["summary"]
        if final.get("next_steps"):
            content += "\n\nNext steps:\n" + "\n".join(f"- {s}" for s in final["next_steps"])
        self._post_message(db, root, content, {
            "task_id": root.id, "status": root.status.value,
            "subtasks": [{"id": c.id, "agent": c.agent.name, "status": c.status.value} for c in children],
        })
        db.commit()
