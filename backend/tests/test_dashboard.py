from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.models import (
    Agent,
    AgentExecution,
    AgentTask,
    Approval,
    Client,
    Project,
    Prospect,
    Revenue,
)
from app.models.enums import (
    ApprovalActionType,
    ApprovalStatus,
    ContractStatus,
    ExecutionStatus,
    ProjectStatus,
    RevenueCategory,
    SalesStage,
    TaskStatus,
)
from app.services import dashboard_service
from app.utils.time import utcnow


def test_dashboard_requires_auth(client: TestClient):
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_empty_dashboard_is_all_zero(founder_client: TestClient):
    r = founder_client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["is_empty"] is True
    k = body["kpis"]
    assert k["total_revenue"] == "0.00"
    assert k["monthly_recurring_revenue"] == "0.00"
    assert all(k[key] == 0 for key in k if key not in ("total_revenue", "monthly_recurring_revenue"))
    assert len(body["charts"]["monthly_revenue"]["labels"]) == 12
    assert body["charts"]["agent_performance"]["labels"] == []
    assert body["activity"]["new_prospects"] == []


def _seed(db):
    stages = [
        SalesStage.NEW, SalesStage.NEW, SalesStage.RESEARCHED, SalesStage.QUALIFIED,
        SalesStage.CONTACTED, SalesStage.MEETING_SCHEDULED, SalesStage.PROPOSAL_SENT,
        SalesStage.NEGOTIATION, SalesStage.WON, SalesStage.LOST,
    ]
    for i, stage in enumerate(stages):
        db.add(Prospect(company_name=f"Co {i}", industry="HVAC", city="Round Rock", stage=stage))
    db.add(Prospect(company_name="Deleted Co", stage=SalesStage.WON, deleted_at=utcnow()))

    active = Client(company_name="A", contract_status=ContractStatus.ACTIVE, monthly_fee=Decimal("1500.00"))
    active2 = Client(company_name="B", contract_status=ContractStatus.ACTIVE, monthly_fee=Decimal("750.50"))
    paused = Client(company_name="C", contract_status=ContractStatus.PAUSED, monthly_fee=Decimal("999.00"))
    db.add_all([active, active2, paused])
    db.flush()

    today = date(2026, 11, 9)
    db.add_all(
        [
            Revenue(category=RevenueCategory.SETUP_FEE, amount=Decimal("0.10"), received_on=date(2026, 11, 1)),
            Revenue(category=RevenueCategory.RETAINER, amount=Decimal("0.20"), received_on=date(2026, 11, 2)),
            Revenue(category=RevenueCategory.RETAINER, amount=Decimal("1500.00"), received_on=date(2026, 10, 15)),
            Revenue(category=RevenueCategory.OTHER, amount=Decimal("100.00"), received_on=date(2024, 1, 1)),
        ]
    )
    db.add_all(
        [
            Project(client_id=active.id, name="P1", status=ProjectStatus.IN_PROGRESS, completion_percent=50),
            Project(client_id=active.id, name="P2", status=ProjectStatus.REVIEW, completion_percent=90),
            Project(client_id=active.id, name="P3", status=ProjectStatus.COMPLETED, completion_percent=100),
            Project(client_id=active.id, name="P4", status=ProjectStatus.PLANNED, completion_percent=0),
        ]
    )
    db.add_all(
        [
            Approval(action_type=ApprovalActionType.SEND_EMAIL, title="Send cold email"),
            Approval(action_type=ApprovalActionType.LAUNCH_ADS, title="Launch ads", status=ApprovalStatus.REJECTED),
        ]
    )
    agent = Agent(key="sales", name="Sales Agent", department="Sales", system_prompt="x")
    db.add(agent)
    db.flush()
    ok_task = AgentTask(title="Research", agent_id=agent.id, status=TaskStatus.COMPLETED)
    bad_task = AgentTask(title="Broken", agent_id=agent.id, status=TaskStatus.FAILED, error="timeout")
    db.add_all([ok_task, bad_task])
    db.flush()
    db.add_all(
        [
            AgentExecution(task_id=ok_task.id, agent_id=agent.id, provider="mock", model="m", status=ExecutionStatus.SUCCEEDED),
            AgentExecution(task_id=bad_task.id, agent_id=agent.id, provider="mock", model="m", status=ExecutionStatus.FAILED),
        ]
    )
    db.commit()
    return today


def test_dashboard_aggregates_real_data(db):
    today = _seed(db)
    data = dashboard_service.summary(db, today=today)
    k = data["kpis"]

    assert data["is_empty"] is False
    assert k["total_revenue"] == "1600.30"  # exact cents, includes old revenue
    assert k["monthly_recurring_revenue"] == "2250.50"  # active clients only
    assert k["total_clients"] == 3
    assert k["active_clients"] == 2
    assert k["total_prospects"] == 10  # soft-deleted excluded
    assert k["qualified_leads"] == 6  # QUALIFIED..WON, no LOST
    assert k["discovery_meetings"] == 4
    assert k["proposals_sent"] == 3
    assert k["closed_deals"] == 1
    assert k["active_projects"] == 2
    assert k["pending_approvals"] == 1

    rev = data["charts"]["monthly_revenue"]
    assert rev["labels"][-1] == "2026-11" and rev["labels"][0] == "2025-12"
    assert rev["values"][-1] == "0.30"
    assert rev["values"][-2] == "1500.00"

    pipe = dict(zip(data["charts"]["pipeline"]["labels"], data["charts"]["pipeline"]["values"]))
    assert pipe["NEW"] == 2 and pipe["LOST"] == 1 and pipe["WON"] == 1

    conv = data["charts"]["lead_conversion"]["values"]
    assert conv == [10, 6, 5, 4, 3, 1]

    proj = data["charts"]["project_completion"]
    assert proj["average_completion"] == 60.0

    agents = data["charts"]["agent_performance"]
    assert agents == {"labels": ["Sales Agent"], "succeeded": [1], "failed": [1]}

    act = data["activity"]
    assert [a["title"] for a in act["pending_approvals"]] == ["Send cold email"]
    assert [t["title"] for t in act["failed_tasks"]] == ["Broken"]
    assert len(act["agent_executions"]) == 2
    assert all(p["company_name"] != "Deleted Co" for p in act["new_prospects"])


def test_dashboard_endpoint_serializes_seeded_data(founder_client: TestClient, db):
    _seed(db)
    body = founder_client.get("/api/v1/dashboard/summary").json()
    assert body["kpis"]["closed_deals"] == 1
    assert body["kpis"]["monthly_recurring_revenue"] == "2250.50"
