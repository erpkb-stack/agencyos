from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.registry import SPECIALIST_TOOLS
from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.models.ai import AgentExecution
from app.models.enums import TaskPriority
from app.models.identity import User
from app.orchestrator import TaskRunner, get_task_runner
from app.providers import PROVIDER_NAMES
from app.schemas.ai import AgentCreate, AgentTestIn, AgentUpdate
from app.security.deps import get_current_user, require_admin, require_writer
from app.services import agent_service, task_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                _: User = Depends(get_current_user)) -> dict[str, Any]:
    agent_service.ensure_seeded(db)
    stats = agent_service.stats_by_agent(db)
    agents = agent_service.list_all(db)
    return {
        "items": [agent_service.serialize(a, settings, stats.get(a.id)) for a in agents],
        "options": {"providers": PROVIDER_NAMES, "custom_agent_tools": SPECIALIST_TOOLS},
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_agent(body: AgentCreate, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                 user: User = Depends(require_admin)) -> dict[str, Any]:
    agent = agent_service.create_custom(db, user, body.model_dump())
    return agent_service.serialize(agent, settings)


@router.get("/{agent_id}")
def get_agent(agent_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
              _: User = Depends(get_current_user)) -> dict[str, Any]:
    agent = agent_service.get_agent(db, agent_id)
    return agent_service.serialize(agent, settings, agent_service.stats_by_agent(db).get(agent.id))


@router.patch("/{agent_id}")
def update_agent(agent_id: int, body: AgentUpdate, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings), user: User = Depends(require_admin)) -> dict[str, Any]:
    agent = agent_service.update(db, user, agent_service.get_agent(db, agent_id), body.model_dump(exclude_unset=True))
    return agent_service.serialize(agent, settings, agent_service.stats_by_agent(db).get(agent.id))


@router.post("/{agent_id}/reset-prompt")
def reset_prompt(agent_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                 user: User = Depends(require_admin)) -> dict[str, Any]:
    agent = agent_service.reset_prompt(db, user, agent_service.get_agent(db, agent_id))
    return agent_service.serialize(agent, settings)


@router.post("/{agent_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_agent(agent_id: int, body: AgentTestIn, db: Session = Depends(get_db),
               user: User = Depends(require_writer), runner: TaskRunner = Depends(get_task_runner)) -> dict[str, Any]:
    """Run the agent once on a test instruction. Proposals still only become approval requests."""
    task = task_service.create_task(
        db, user, agent_id=agent_id, title=f"Test: {body.instructions[:120]}",
        instructions=body.instructions, priority=TaskPriority.LOW, task_type="agent_test",
    )
    runner.submit(task.id)
    return {"task_id": task.id}


@router.get("/{agent_id}/executions")
def list_executions(agent_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict[str, Any]:
    agent_service.get_agent(db, agent_id)
    query = select(AgentExecution).where(AgentExecution.agent_id == agent_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(AgentExecution.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [task_service.execution_out(e) for e in rows], "total": total,
            "page": page, "page_size": page_size}
