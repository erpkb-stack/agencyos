"""Phase 5: invoices, payments, revenue, expenses, summaries, profitability and reports."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.approvals import Approval
from app.models.enums import UserRole
from app.services.finance_service import money
from tests.conftest import login

API = "/api/v1"
TODAY = date.today()


def make_client(c: TestClient, name: str = "Bright Plumbing", fee: str = "1500", setup: str = "500") -> dict:
    r = c.post(f"{API}/clients", json={"company_name": name, "contract_status": "ACTIVE", "monthly_fee": fee,
                                       "setup_fee": setup, "service_package": "Growth"})
    assert r.status_code == 201, r.text
    return r.json()


def test_money_rounding_is_exact():
    assert money("0.1") + money("0.2") == Decimal("0.30")
    assert money(Decimal("2.675")) == Decimal("2.68")  # half-up, no float artefacts
    assert money(Decimal("3") * Decimal("33.33")) == Decimal("99.99")


def test_invoice_lifecycle_paid_only_by_payments(founder_client: TestClient):
    client = make_client(founder_client)
    inv = founder_client.post(f"{API}/finance/invoices/from-client/{client['id']}",
                              json={"include_setup_fee": True, "period_label": "March 2026"}).json()
    assert inv["status"] == "DRAFT" and inv["number"].startswith(f"INV-{TODAY.year}-")
    assert [i["amount"] for i in inv["items"]] == ["1500.00", "500.00"] and inv["total"] == "2000.00"
    assert "March 2026" in inv["items"][0]["description"]
    base = f"{API}/finance/invoices/{inv['id']}"

    edited = founder_client.patch(base, json={"items": [{"description": "Retainer", "quantity": "3", "unit_price": "33.33"}]}).json()
    assert edited["total"] == "99.99"
    founder_client.patch(base, json={"items": [{"description": "Retainer", "quantity": "1", "unit_price": "1500"},
                                               {"description": "Setup", "quantity": "1", "unit_price": "500"}]})

    pay = {"invoice_id": inv["id"], "amount": "500", "paid_on": TODAY.isoformat(), "method": "ACH"}
    assert founder_client.post(f"{API}/finance/payments", json=pay).status_code == 400  # draft: not sent yet
    sent = founder_client.post(f"{base}/mark-sent").json()
    assert sent["status"] == "SENT" and sent["due_date"]
    assert founder_client.patch(base, json={"notes": "x"}).status_code == 400  # no edits after sending

    p1 = founder_client.post(f"{API}/finance/payments", json=pay).json()
    after = founder_client.get(base).json()
    assert after["status"] == "PARTIALLY_PAID" and after["paid"] == "500.00" and after["balance"] == "1500.00"
    over = founder_client.post(f"{API}/finance/payments", json={**pay, "amount": "1500.01"})
    assert over.status_code == 400 and "balance" in over.json()["error"]["message"]
    future = founder_client.post(f"{API}/finance/payments", json={**pay, "paid_on": (TODAY + timedelta(days=2)).isoformat()})
    assert future.status_code == 400
    founder_client.post(f"{API}/finance/payments", json={**pay, "amount": "1500"})
    assert founder_client.get(base).json()["status"] == "PAID"

    revenue = founder_client.get(f"{API}/finance/revenue").json()["items"]
    assert sorted(r["amount"] for r in revenue) == ["1500.00", "500.00"] and all(r["from_payment"] for r in revenue)
    assert founder_client.patch(f"{API}/finance/revenue/{revenue[0]['id']}", json={"amount": "1"}).status_code == 400
    assert founder_client.post(f"{base}/void").status_code == 409
    assert founder_client.delete(base).status_code == 400

    assert founder_client.delete(f"{API}/finance/payments/{p1['id']}").status_code == 204
    back = founder_client.get(base).json()
    assert back["status"] == "PARTIALLY_PAID" and back["paid"] == "1500.00"
    assert len(founder_client.get(f"{API}/finance/revenue").json()["items"]) == 1
    printable = founder_client.get(f"{base}/print")
    assert printable.status_code == 200 and inv["number"] in printable.text
    assert "default-src 'none'" in printable.headers["content-security-policy"]


def test_draft_invoice_delete_and_void(founder_client: TestClient):
    client = make_client(founder_client)
    body = {"client_id": client["id"], "items": [{"description": "Audit", "unit_price": "250"}]}
    d = founder_client.post(f"{API}/finance/invoices", json=body).json()
    assert founder_client.delete(f"{API}/finance/invoices/{d['id']}").status_code == 204
    s = founder_client.post(f"{API}/finance/invoices", json=body).json()
    founder_client.post(f"{API}/finance/invoices/{s['id']}/mark-sent")
    assert founder_client.post(f"{API}/finance/invoices/{s['id']}/void").json()["status"] == "VOID"
    pay = founder_client.post(f"{API}/finance/payments", json={"invoice_id": s["id"], "amount": "10", "paid_on": TODAY.isoformat()})
    assert pay.status_code == 400
    bad = founder_client.post(f"{API}/finance/invoices", json={**body, "issue_date": "2026-03-10", "due_date": "2026-03-01"})
    assert bad.status_code == 400
    assert founder_client.post(f"{API}/finance/invoices", json={**body, "items": []}).status_code == 422
    neg = founder_client.post(f"{API}/finance/invoices", json={**body, "items": [{"description": "x", "unit_price": "-5"}]})
    assert neg.status_code == 422


def test_summary_outstanding_overdue_and_dashboard(founder_client: TestClient):
    client = make_client(founder_client)
    month_start = TODAY.replace(day=1).isoformat()
    founder_client.post(f"{API}/finance/revenue", json={"client_id": client["id"], "category": "SETUP_FEE",
                                                         "amount": "500", "received_on": month_start})
    founder_client.post(f"{API}/finance/expenses", json={"category": "HOSTING", "vendor": "Host Co", "amount": "120.50",
                                                          "incurred_on": month_start, "is_recurring": True})
    founder_client.post(f"{API}/finance/expenses", json={"category": "SOFTWARE", "amount": "79.50", "incurred_on": month_start})
    old = founder_client.post(f"{API}/finance/invoices", json={
        "client_id": client["id"], "issue_date": (TODAY - timedelta(days=40)).isoformat(),
        "due_date": (TODAY - timedelta(days=10)).isoformat(), "items": [{"description": "Retainer", "unit_price": "1500"}]}).json()
    founder_client.post(f"{API}/finance/invoices/{old['id']}/mark-sent")
    founder_client.post(f"{API}/finance/payments", json={"invoice_id": old["id"], "amount": "300", "paid_on": TODAY.isoformat()})

    s = founder_client.get(f"{API}/finance/summary").json()
    assert s["total_revenue"] == "800.00" and s["total_expenses"] == "200.00" and s["gross_profit"] == "600.00"
    assert s["margin_percent"] == 75.0 and s["monthly_recurring_revenue"] == "1500.00"
    assert s["outstanding"] == "1200.00" and s["overdue"] == "1200.00" and s["overdue_invoices"] == 1
    assert s["expenses_by_category"]["HOSTING"] == "120.50"
    assert s["monthly"]["revenue"][-1] == "800.00" and s["monthly"]["profit"][-1] == "600.00"
    prof = s["profitability"]["rows"][0]
    assert prof["revenue"] == "800.00" and prof["allocated_expenses"] == "200.00" and prof["estimated_profit"] == "600.00"
    assert founder_client.get(f"{API}/finance/invoices?overdue=true").json()["items"][0]["id"] == old["id"]
    assert founder_client.get(f"{API}/dashboard/summary").json()["kpis"]["total_revenue"] == "800.00"
    assert founder_client.get(f"{API}/finance/summary?start=2026-05-01&end=2026-04-01").status_code == 400


def test_profitability_allocates_by_revenue_share(founder_client: TestClient):
    a, b = make_client(founder_client, "A Co"), make_client(founder_client, "B Co")
    d = TODAY.replace(day=1).isoformat()
    for cid, amt in ((a["id"], "750"), (b["id"], "250")):
        founder_client.post(f"{API}/finance/revenue", json={"client_id": cid, "category": "RETAINER", "amount": amt, "received_on": d})
    founder_client.post(f"{API}/finance/expenses", json={"category": "SOFTWARE", "amount": "100", "incurred_on": d})
    rows = {r["client_name"]: r for r in founder_client.get(f"{API}/finance/summary").json()["profitability"]["rows"]}
    assert rows["A Co"]["allocated_expenses"] == "75.00" and rows["A Co"]["estimated_profit"] == "675.00"
    assert rows["B Co"]["allocated_expenses"] == "25.00" and rows["B Co"]["margin_percent"] == 90.0


def test_expenses_and_ai_usage(founder_client: TestClient):
    e = founder_client.post(f"{API}/finance/expenses", json={"category": "DOMAINS", "amount": "12", "incurred_on": TODAY.isoformat()}).json()
    assert founder_client.patch(f"{API}/finance/expenses/{e['id']}", json={"amount": "15.5"}).json()["amount"] == "15.50"
    assert founder_client.delete(f"{API}/finance/expenses/{e['id']}").status_code == 204
    assert founder_client.get(f"{API}/finance/expenses").json()["items"] == []
    assert founder_client.post(f"{API}/finance/expenses", json={"category": "OTHER", "amount": "0", "incurred_on": TODAY.isoformat()}).status_code == 422

    founder_client.post(f"{API}/ceo/messages", json={"content": "Show me the agency's financial position."})
    month = TODAY.strftime("%Y-%m")
    usage = founder_client.get(f"{API}/finance/ai-usage?month={month}").json()
    assert usage["by_model"][0]["provider"] == "mock" and usage["by_model"][0]["calls"] >= 3
    assert usage["estimated_total"] == "0.00"
    exp = founder_client.post(f"{API}/finance/ai-usage/expense", json={"month": month, "amount": "42.10", "vendor": "Anthropic"}).json()
    assert exp["category"] == "AI_API" and exp["description"] == f"AI API usage {month}"
    assert founder_client.get(f"{API}/finance/ai-usage?month={month}").json()["recorded_ai_expenses"] == "42.10"


def test_finance_roles(client: TestClient, make_user):
    make_user()
    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    make_user(email="ro@example.com", role=UserRole.READ_ONLY, name="Rita Reader")
    staff = login(TestClient(client.app), "staff@example.com")
    rev = {"category": "OTHER", "amount": "10", "received_on": TODAY.isoformat()}
    assert staff.post(f"{API}/finance/revenue", json=rev).status_code == 403  # manual revenue: Founder/Admin only
    assert staff.post(f"{API}/finance/expenses", json={"category": "OTHER", "amount": "5", "incurred_on": TODAY.isoformat()}).status_code == 201
    ro = login(TestClient(client.app), "ro@example.com")
    assert ro.get(f"{API}/finance/summary").status_code == 200
    assert ro.post(f"{API}/finance/payments", json={"client_id": 1, "amount": "5", "paid_on": TODAY.isoformat()}).status_code == 403


# -- reports -----------------------------------------------------------------------------------


def test_every_report_builds_and_exports(founder_client: TestClient):
    make_client(founder_client)
    founder_client.post(f"{API}/prospects", json={"company_name": "Referral Co", "lead_source": "Referral", "stage": "QUALIFIED"})
    founder_client.post(f"{API}/prospects", json={"company_name": "Evil Co", "lead_source": '=HYPERLINK("http://x")'})
    founder_client.post(f"{API}/ceo/messages", json={"content": "Give me the status of all active projects."})
    keys = [r["key"] for r in founder_client.get(f"{API}/analytics/reports").json()["items"]]
    assert len(keys) == 9
    for key in keys:
        r = founder_client.get(f"{API}/analytics/reports/{key}")
        assert r.status_code == 200, (key, r.text)
        body = r.json()
        assert body["kpis"] and body["sections"] and body["period"]["start"]
        exported = founder_client.get(f"{API}/analytics/reports/{key}/csv")
        assert exported.headers["content-type"].startswith("text/csv") and "attachment" in exported.headers["content-disposition"]
        assert body["title"] in exported.text

    perf = founder_client.get(f"{API}/analytics/reports/prospect_performance").json()
    by_source = next(s for s in perf["sections"] if s["title"] == "By lead source")
    assert ["Referral", 1, 1] in [r[:3] for r in by_source["rows"]]
    perf_csv = founder_client.get(f"{API}/analytics/reports/prospect_performance/csv").text
    assert "'=HYPERLINK" in perf_csv  # a cell starting with = is neutralised
    agents = founder_client.get(f"{API}/analytics/reports/agent_performance").json()
    assert agents["sections"][0]["rows"] and agents["kpis"][1]["value"] == "100.0%"

    assert founder_client.get(f"{API}/analytics/reports/nope").status_code == 404
    assert founder_client.get(f"{API}/analytics/reports/financial_summary?start=2026-05-02&end=2026-05-01").status_code == 400
    rows = list(csv.reader(io.StringIO(founder_client.get(f"{API}/analytics/reports/sales_conversion/csv").text)))
    assert rows[0] == ["Sales conversion"] and ["Metric", "Value"] in rows


def test_report_summary_is_draft_only(founder_client: TestClient, db):
    before = db.scalar(select(func.count()).select_from(Approval))
    r = founder_client.post(f"{API}/analytics/reports/weekly_ceo/summarize")
    assert r.status_code == 202
    task = founder_client.get(f"{API}/tasks/{r.json()['task_id']}").json()
    assert task["agent"]["key"] == "analytics" and task["status"] == "COMPLETED"
    assert "report" in task["input"]["context"]
    assert founder_client.get(f"{API}/analytics/summaries").json()["items"][0]["id"] == task["id"]
    assert db.scalar(select(func.count()).select_from(Approval)) == before
