"""Phase 7: parallel specialists, durable task queue and AI cost limits."""

from __future__ import annotations

import threading
import time
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import get_settings
from app.models.ai import AgentExecution, AgentTask
from app.models.enums import TaskPriority, TaskStatus as S
from app.orchestrator.engine import Orchestrator
from app.orchestrator.runner import QueueWorker, claim_next, queue_depth, recover_interrupted
from app.providers.mock import MockLLMProvider

API = "/api/v1"
THREE = ("Plan our HVAC lead-generation launch: research the target market strategy, prepare a cold email "
         "sequence, and create a landing page.")


class SlowSpecialists(MockLLMProvider):
    """Each specialist call sleeps; records how many run at the same time."""

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def generate(self, request):
        if request.schema_name == "SpecialistOutput":
            with self._lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(self.delay)
            finally:
                with self._lock:
                    self.active -= 1
        return super().generate(request)


class Priced(MockLLMProvider):
    """Mock output, but every call costs a fixed amount."""

    def __init__(self, cost: str) -> None:
        self.cost = Decimal(cost)

    def generate(self, request):
        response = super().generate(request)
        response.estimated_cost = self.cost
        return response


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


# -- parallel specialists ----------------------------------------------------------------------


def test_specialists_run_in_parallel(founder_client: TestClient, llm, orchestrator):
    llm.provider = SlowSpecialists(0.4)
    started = time.monotonic()
    created = founder_client.post(f"{API}/ceo/messages", json={"content": THREE}).json()
    elapsed = time.monotonic() - started
    task = founder_client.get(f"{API}/tasks/{created['task_id']}").json()
    assert task["status"] == "COMPLETED" and len(task["children"]) == 3
    assert llm.provider.peak == 3  # all three specialists were working at once
    assert elapsed < 3 * 0.4 + 1.0  # sequential would need at least 1.2 s of sleeping alone


def test_parallelism_is_configurable(founder_client: TestClient, llm, orchestrator):
    llm.provider = SlowSpecialists(0.1)
    orchestrator.settings = _settings(orchestrator_parallel_agents=1)
    created = founder_client.post(f"{API}/ceo/messages", json={"content": THREE}).json()
    assert founder_client.get(f"{API}/tasks/{created['task_id']}").json()["status"] == "COMPLETED"
    assert llm.provider.peak == 1


def test_one_failing_specialist_does_not_stop_the_others(founder_client: TestClient, llm):
    from app.providers.base import ProviderError

    class FailEmail(MockLLMProvider):
        def generate(self, request):
            if request.mock_context.get("agent_key") == "email_marketing":
                raise ProviderError("email provider down")
            return super().generate(request)

    llm.provider = FailEmail()
    created = founder_client.post(f"{API}/ceo/messages", json={"content": THREE}).json()
    task = founder_client.get(f"{API}/tasks/{created['task_id']}").json()
    statuses = {c["agent"]["key"]: c["status"] for c in task["children"]}
    assert statuses["email_marketing"] == "FAILED"
    assert statuses["business_strategy"] == "COMPLETED" and statuses["funnel"] == "AWAITING_APPROVAL"
    assert task["status"] == "COMPLETED"


# -- cost limits -------------------------------------------------------------------------------


def test_per_request_cost_limit_stops_further_calls(founder_client: TestClient, llm, orchestrator, db):
    llm.provider = Priced("0.30")
    orchestrator.settings = _settings(ai_cost_limit_per_request=Decimal("0.50"), orchestrator_parallel_agents=1)
    created = founder_client.post(f"{API}/ceo/messages", json={"content": THREE}).json()
    task = founder_client.get(f"{API}/tasks/{created['task_id']}").json()
    statuses = [c["status"] for c in task["children"]]
    # Plan ($0.30) + first specialist ($0.30) = $0.60 ≥ $0.50: later calls are refused.
    assert statuses[0] in ("COMPLETED", "AWAITING_APPROVAL")
    assert statuses[1:] == ["FAILED", "FAILED"]
    assert all("cost limit" in c["error"] for c in task["children"][1:])
    assert task["request_cost"] == "0.6000"
    assert task["output"]["final"]["summary"].startswith("Automatic summary")  # summary call refused too
    assert len(list(db.scalars(select(AgentExecution)))) == 2


def test_monthly_cost_limit_refuses_new_work(founder_client: TestClient, llm, orchestrator, monkeypatch):
    llm.provider = Priced("1.00")
    founder_client.post(f"{API}/ceo/messages", json={"content": "Prepare a cold email sequence"})
    capped = _settings(ai_cost_limit_per_month=Decimal("2.00"))
    monkeypatch.setattr("app.config.settings.get_settings", lambda: capped)
    r = founder_client.post(f"{API}/ceo/messages", json={"content": "Another request"})
    assert r.status_code == 409 and "Monthly AI cost limit" in r.json()["error"]["message"]
    agents = founder_client.get(f"{API}/agents").json()["items"]
    r = founder_client.post(f"{API}/tasks", json={"agent_id": agents[1]["id"], "title": "x", "instructions": "y"})
    assert r.status_code == 409
    system = founder_client.get(f"{API}/settings/system").json()["orchestrator"]
    # Plan $1 + specialist $1; the CEO summary was refused by the default $2 per-request limit.
    assert Decimal(system["month_spend"]) == Decimal("2")


def test_zero_means_no_limit(founder_client: TestClient, llm, orchestrator):
    llm.provider = Priced("5.00")
    orchestrator.settings = _settings(ai_cost_limit_per_request=Decimal("0"))
    created = founder_client.post(f"{API}/ceo/messages", json={"content": THREE}).json()
    task = founder_client.get(f"{API}/tasks/{created['task_id']}").json()
    assert all(c["status"] != "FAILED" for c in task["children"])


# -- durable queue -----------------------------------------------------------------------------


def _queued_task(db, agent_id: int, priority: TaskPriority = TaskPriority.NORMAL) -> int:
    task = AgentTask(agent_id=agent_id, title=f"queued {priority.value}", priority=priority, status=S.PENDING,
                     input={"instructions": "Design a workflow", "context": {}})
    db.add(task)
    db.commit()
    return task.id


def test_claiming_is_atomic_and_by_priority(founder_client: TestClient, db, session_factory):
    agent_id = founder_client.get(f"{API}/agents").json()["items"][1]["id"]
    low = _queued_task(db, agent_id, TaskPriority.LOW)
    normal = _queued_task(db, agent_id, TaskPriority.NORMAL)
    urgent = _queued_task(db, agent_id, TaskPriority.URGENT)
    assert queue_depth(db) == 3

    claimed: list[int] = []
    lock = threading.Lock()

    def grab():
        with session_factory() as s:
            ids = claim_next(s, 1)
        with lock:
            claimed.extend(ids)

    threads = [threading.Thread(target=grab) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(claimed) == sorted([low, normal, urgent])  # each claimed exactly once
    with session_factory() as s:
        assert claim_next(s, 1) == []
    db.expire_all()
    assert all(db.get(AgentTask, i).status == S.ASSIGNED for i in (low, normal, urgent))

    first = _queued_task(db, agent_id, TaskPriority.LOW)
    second = _queued_task(db, agent_id, TaskPriority.HIGH)
    with session_factory() as s:
        assert claim_next(s, 2) == [second, first]


def test_queue_worker_runs_queued_tasks_and_survives_restart(founder_client: TestClient, db, session_factory, llm):
    agent_id = founder_client.get(f"{API}/agents").json()["items"][1]["id"]
    ids = [_queued_task(db, agent_id) for _ in range(3)]
    # A request that was mid-run when the "server" died, with an attempt left:
    interrupted = db.get(AgentTask, _queued_task(db, agent_id))
    interrupted.status, interrupted.attempts = S.RUNNING, 1
    db.commit()
    assert recover_interrupted(db) == {"requeued": 1, "failed": 0}

    worker = QueueWorker(Orchestrator(session_factory, llm, get_settings()), session_factory,
                         workers=2, poll_seconds=0.1).start()
    try:
        assert worker.wait_idle(timeout=20)
    finally:
        worker.shutdown(wait=True)
    db.expire_all()
    for task_id in [*ids, interrupted.id]:
        assert db.get(AgentTask, task_id).status == S.COMPLETED, task_id
    assert db.get(AgentTask, interrupted.id).attempts == 2


def test_cancelled_queued_task_is_never_run(founder_client: TestClient, db, session_factory, llm):
    agent_id = founder_client.get(f"{API}/agents").json()["items"][1]["id"]
    task_id = _queued_task(db, agent_id)
    assert founder_client.post(f"{API}/tasks/{task_id}/cancel").status_code == 200
    with session_factory() as s:
        assert claim_next(s, 5) == []
    assert founder_client.get(f"{API}/tasks/{task_id}").json()["status"] == "CANCELLED"


def test_queued_phase_and_queue_info(founder_client: TestClient, db):
    agent_id = founder_client.get(f"{API}/agents").json()["items"][1]["id"]
    task_id = _queued_task(db, agent_id)
    assert founder_client.get(f"{API}/tasks/{task_id}").json()["phase"] == "QUEUED"
    orch = founder_client.get(f"{API}/settings/system").json()["orchestrator"]
    assert orch["queued"] == 1 and orch["parallel_agents"] == 3 and orch["per_request_limit"] == "2.00"
