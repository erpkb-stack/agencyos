"""Phase 3: prospects (CRUD, search, duplicates, CSV, conversion), clients, projects."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from app.models.enums import UserRole
from tests.conftest import login

API = "/api/v1"


def make_prospect(c: TestClient, **kw) -> dict:
    body = {"company_name": "Cool Air HVAC", "city": "Austin", "industry": "HVAC", **kw}
    r = c.post(f"{API}/prospects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# -- prospects ---------------------------------------------------------------------------------


def test_prospect_crud_and_validation(founder_client: TestClient):
    p = make_prospect(founder_client, website="www.CoolAir.com/contact", email="Owner@CoolAir.com",
                      phone="(512) 555-0100", lead_score=70)
    assert p["website"] == "https://www.CoolAir.com/contact"
    assert p["normalized_domain"] == "coolair.com"
    assert p["email"] == "owner@coolair.com"
    assert p["stage"] == "NEW"

    r = founder_client.patch(f"{API}/prospects/{p['id']}", json={"stage": "QUALIFIED", "notes": "Busy summer"})
    assert r.json()["stage"] == "QUALIFIED"
    detail = founder_client.get(f"{API}/prospects/{p['id']}").json()
    kinds = [a["activity_type"] for a in detail["activities"]]
    assert "stage_change" in kinds and "created" in kinds

    bad = founder_client.post(f"{API}/prospects", json={"company_name": "X", "email": "nope", "lead_score": 120})
    assert bad.status_code == 422
    fields = {d["field"] for d in bad.json()["error"]["details"]}
    assert {"body.email", "body.lead_score"} <= fields

    assert founder_client.delete(f"{API}/prospects/{p['id']}").status_code == 204
    assert founder_client.get(f"{API}/prospects/{p['id']}").status_code == 404
    assert founder_client.get(f"{API}/prospects").json()["total"] == 0


def test_duplicate_detection(founder_client: TestClient):
    first = make_prospect(founder_client, website="https://coolair.com", phone="512-555-0100")
    for dupe in ({"company_name": "Other", "website": "http://www.coolair.com/about"},
                 {"company_name": "Other", "phone": "+1 (512) 555 0100"},
                 {"company_name": "Cool Air HVAC, LLC", "city": "austin"}):
        r = founder_client.post(f"{API}/prospects", json=dupe)
        assert r.status_code == 409, dupe
        assert r.json()["error"]["details"]["duplicates"][0]["id"] == first["id"]
    ok = founder_client.post(f"{API}/prospects", json={"company_name": "Cool Air HVAC", "city": "Dallas"})
    assert ok.status_code == 201  # same name, different city
    forced = founder_client.post(f"{API}/prospects", json={"company_name": "Other", "website": "coolair.com",
                                                           "allow_duplicate": True})
    assert forced.status_code == 201


def test_search_filter_sort_paginate(founder_client: TestClient):
    for i, (name, city, score, stage) in enumerate([
        ("Alpha Plumbing", "Austin", 30, "NEW"), ("Bravo Roofing", "Dallas", 90, "QUALIFIED"),
        ("Charlie HVAC", "Austin", 60, "QUALIFIED"), ("Delta Accounting", "Houston", 10, "LOST"),
    ]):
        make_prospect(founder_client, company_name=name, city=city, lead_score=score, stage=stage,
                      industry="Trades", website=f"site{i}.com")
    get = lambda qs: founder_client.get(f"{API}/prospects?{qs}").json()
    assert get("q=bravo")["total"] == 1
    assert get("stage=QUALIFIED")["total"] == 2
    assert get("city=austin")["total"] == 2
    assert get("min_score=50")["total"] == 2
    ranked = get("sort=lead_score&order=desc")["items"]
    assert [p["lead_score"] for p in ranked] == [90, 60, 30, 10]
    page2 = get("sort=company_name&order=asc&page=2&page_size=3")
    assert page2["total"] == 4 and [p["company_name"] for p in page2["items"]] == ["Delta Accounting"]
    assert "Austin" in page2["facets"]["cities"]
    assert founder_client.get(f"{API}/prospects?sort=password").status_code == 422


def test_csv_import_and_export(founder_client: TestClient):
    make_prospect(founder_client, website="existing.com")
    data = (
        "Company,Website,City,Business Email,Score,Sales Stage,Unknown Column\n"
        "Peak Roofing,peakroofing.com,Austin,info@peakroofing.com,55,qualified,x\n"
        "Dup Co,https://existing.com,Austin,,,,\n"
        "Bad Email Co,bad.com,Austin,not-an-email,,,\n"
        ",,,,,,\n"
        "=HYPERLINK(\"evil\"),evil.com,Austin,,,,\n"
    ).encode()
    r = founder_client.post(f"{API}/prospects/import", files={"file": ("leads.csv", data, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    assert [s["row"] for s in body["skipped_duplicates"]] == [3]
    assert body["errors"][0]["row"] == 4 and "email" in body["errors"][0]["error"]
    assert body["ignored_columns"] == ["Unknown Column"]
    peak = founder_client.get(f"{API}/prospects?q=peak").json()["items"][0]
    assert peak["stage"] == "QUALIFIED" and peak["lead_score"] == 55

    export = founder_client.get(f"{API}/prospects/export?sort=company_name&order=asc")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 3
    evil = next(r for r in rows if "HYPERLINK" in r["company_name"])
    assert evil["company_name"].startswith("'=")  # formula injection neutralised

    assert founder_client.post(f"{API}/prospects/import", files={"file": ("x.txt", b"a", "text/plain")}).status_code == 400
    no_name = founder_client.post(f"{API}/prospects/import", files={"file": ("x.csv", b"city\nAustin\n", "text/csv")})
    assert no_name.status_code == 400


def test_activity_and_follow_up(founder_client: TestClient):
    p = make_prospect(founder_client, next_follow_up_on="2000-01-01")
    r = founder_client.post(f"{API}/prospects/{p['id']}/activities",
                            json={"activity_type": "call", "description": "Left voicemail", "mark_contacted": True})
    assert r.status_code == 201
    assert r.json()["activities"][0]["description"] == "Left voicemail"
    detail = founder_client.get(f"{API}/prospects/{p['id']}").json()
    assert detail["last_contacted_at"] is not None
    assert founder_client.get(f"{API}/prospects?follow_up_due=true").json()["total"] == 1


def test_convert_prospect_to_client(founder_client: TestClient):
    p = make_prospect(founder_client, contact_name="Ana", email="ana@coolair.com", address="1 Main St", state="TX")
    r = founder_client.post(f"{API}/prospects/{p['id']}/convert",
                            json={"monthly_fee": "1500.00", "setup_fee": "500", "service_package": "Growth"})
    assert r.status_code == 201
    client = r.json()
    assert client["contract_status"] == "ACTIVE" and client["monthly_fee"] == "1500.00"
    assert client["address"] == "1 Main St, Austin, TX"
    assert founder_client.get(f"{API}/prospects/{p['id']}").json()["stage"] == "WON"
    assert founder_client.post(f"{API}/prospects/{p['id']}/convert", json={}).status_code == 409
    kpis = founder_client.get(f"{API}/dashboard/summary").json()["kpis"]
    assert kpis["active_clients"] == 1 and kpis["monthly_recurring_revenue"] == "1500.00"


def test_sales_agent_researches_a_prospect_record(founder_client: TestClient):
    p = make_prospect(founder_client, observations="No online booking form")
    r = founder_client.post(f"{API}/prospects/{p['id']}/research", json={})
    assert r.status_code == 202
    task = founder_client.get(f"{API}/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "COMPLETED" and task["agent"]["key"] == "sales"
    report = task["output"]["result"]["deliverables"][0]["content"]
    assert "Cool Air HVAC" in report
    acts = founder_client.get(f"{API}/prospects/{p['id']}").json()["activities"]
    assert acts[0]["activity_type"] == "agent_task" and acts[0]["agent"] == "Sales Agent"


def test_read_only_cannot_write_crm(client: TestClient, make_user):
    make_user(email="ro@example.com", role=UserRole.READ_ONLY, name="Rita Reader")
    ro = login(client, "ro@example.com")
    assert ro.get(f"{API}/prospects").status_code == 200
    assert ro.post(f"{API}/prospects", json={"company_name": "X"}).status_code == 403
    assert ro.post(f"{API}/clients", json={"company_name": "X"}).status_code == 403
    assert ro.post(f"{API}/projects", json={"client_id": 1, "name": "X"}).status_code == 403


# -- clients -----------------------------------------------------------------------------------


def make_client(c: TestClient, **kw) -> dict:
    r = c.post(f"{API}/clients", json={"company_name": "Bright Plumbing", "contract_status": "ACTIVE",
                                       "monthly_fee": "900", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_client_crud(founder_client: TestClient):
    c = make_client(founder_client, start_date="2026-01-01", email="hello@bright.com")
    assert c["monthly_fee"] == "900.00"
    assert founder_client.post(f"{API}/clients", json={"company_name": "bright plumbing"}).status_code == 409
    bad = founder_client.patch(f"{API}/clients/{c['id']}", json={"end_date": "2025-01-01"})
    assert bad.status_code == 400
    r = founder_client.patch(f"{API}/clients/{c['id']}", json={"contract_status": "PAUSED"})
    assert r.json()["contract_status"] == "PAUSED"
    assert founder_client.get(f"{API}/clients?contract_status=PAUSED").json()["total"] == 1
    detail = founder_client.get(f"{API}/clients/{c['id']}").json()
    assert "Contract ACTIVE → PAUSED" in detail["activity"][0]["summary"]
    assert detail["projects"] == [] and detail["invoices"] == []
    assert founder_client.patch(f"{API}/clients/{c['id']}", json={"monthly_fee": "-1"}).status_code == 422


def test_client_with_active_project_cannot_be_deleted(founder_client: TestClient):
    c = make_client(founder_client)
    p = founder_client.post(f"{API}/projects", json={"client_id": c["id"], "name": "Site", "status": "IN_PROGRESS"}).json()
    assert founder_client.delete(f"{API}/clients/{c['id']}").status_code == 409
    founder_client.patch(f"{API}/projects/{p['id']}", json={"status": "COMPLETED"})
    assert founder_client.delete(f"{API}/clients/{c['id']}").status_code == 204


def test_staff_cannot_delete_clients(client: TestClient, make_user):
    make_user()
    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    founder = login(TestClient(client.app), "founder@example.com")
    c = make_client(founder)
    staff = login(TestClient(client.app), "staff@example.com")
    assert staff.patch(f"{API}/clients/{c['id']}", json={"notes": "ok"}).status_code == 200
    assert staff.delete(f"{API}/clients/{c['id']}").status_code == 403


# -- projects ----------------------------------------------------------------------------------


def make_project(c: TestClient, client_name: str = "Bright Plumbing", **kw) -> dict:
    client = make_client(c, company_name=client_name)
    r = c.post(f"{API}/projects", json={"client_id": client["id"], "name": "Website rebuild",
                                        "scope": "5-page site", "budget": "3000", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_project_tasks_dependencies_and_progress(founder_client: TestClient):
    p = make_project(founder_client)
    base = f"{API}/projects/{p['id']}/tasks"
    design = founder_client.post(base, json={"title": "Design"}).json()
    build = founder_client.post(base, json={"title": "Build", "depends_on_task_id": design["id"]}).json()
    assert build["blocked"] is True

    blocked = founder_client.patch(f"{base}/{build['id']}", json={"status": "IN_PROGRESS"})
    assert blocked.status_code == 400 and "Blocked" in blocked.json()["error"]["message"]
    cycle = founder_client.patch(f"{base}/{design['id']}", json={"depends_on_task_id": build["id"]})
    assert cycle.status_code == 400 and "cycle" in cycle.json()["error"]["message"]

    founder_client.patch(f"{base}/{design['id']}", json={"status": "DONE"})
    assert founder_client.get(f"{API}/projects/{p['id']}").json()["completion_percent"] == 50
    assert founder_client.patch(f"{base}/{build['id']}", json={"status": "DONE"}).status_code == 200
    detail = founder_client.get(f"{API}/projects/{p['id']}").json()
    assert detail["completion_percent"] == 100 and detail["completion_from_tasks"]
    assert detail["tasks"][1]["completed_at"] is not None
    manual = founder_client.patch(f"{API}/projects/{p['id']}", json={"completion_percent": 20})
    assert manual.status_code == 400

    other = make_project(founder_client, client_name="Other Client", name="Other")
    other_task = founder_client.post(f"{API}/projects/{other['id']}/tasks", json={"title": "x"}).json()
    cross = founder_client.post(base, json={"title": "y", "depends_on_task_id": other_task["id"]})
    assert cross.status_code == 404


def test_scope_cannot_change_silently(founder_client: TestClient):
    p = make_project(founder_client)
    r = founder_client.patch(f"{API}/projects/{p['id']}", json={"scope": "10-page site plus blog"})
    assert r.status_code == 400 and "reason" in r.json()["error"]["message"]
    r = founder_client.patch(f"{API}/projects/{p['id']}", json={
        "scope": "10-page site plus blog", "scope_change_reason": "Client signed change order #2"})
    assert r.status_code == 200
    timeline = founder_client.get(f"{API}/projects/{p['id']}").json()["timeline"]
    assert any(e["kind"] == "comment" and "change order #2" in e["text"] for e in timeline)


def test_project_status_comments_deliverables_agents(founder_client: TestClient):
    agents = {a["key"]: a for a in founder_client.get(f"{API}/agents").json()["items"]}
    p = make_project(founder_client, agent_ids=[agents["funnel"]["id"]], start_date="2026-02-01", due_date="2026-03-01")
    assert [a["name"] for a in p["agents"]] == ["Funnel Agent"]
    assert founder_client.patch(f"{API}/projects/{p['id']}", json={"due_date": "2026-01-01"}).status_code == 400
    r = founder_client.patch(f"{API}/projects/{p['id']}", json={"status": "IN_PROGRESS"})
    assert r.json()["status"] == "IN_PROGRESS"

    founder_client.post(f"{API}/projects/{p['id']}/comments", json={"body": "Kickoff done"})
    d = founder_client.post(f"{API}/projects/{p['id']}/deliverables",
                            json={"title": "Homepage mockup", "url": "figma.com/file/x"}).json()
    assert d["status"] == "DRAFT" and d["url"] == "https://figma.com/file/x"
    d2 = founder_client.patch(f"{API}/projects/{p['id']}/deliverables/{d['id']}", json={"status": "DELIVERED"}).json()
    assert d2["delivered_at"] is not None

    detail = founder_client.get(f"{API}/projects/{p['id']}").json()
    texts = [e["text"] for e in detail["timeline"]]
    assert "Kickoff done" in texts and any("PLANNED → IN_PROGRESS" in t for t in texts)
    client_view = founder_client.get(f"{API}/clients/{p['client_id']}").json()
    assert client_view["deliverables"][0]["title"] == "Homepage mockup"
    assert founder_client.get(f"{API}/projects?status=IN_PROGRESS").json()["total"] == 1
    assert founder_client.get(f"{API}/dashboard/summary").json()["kpis"]["active_projects"] == 1


def test_project_task_can_be_sent_to_its_agent(founder_client: TestClient):
    agents = {a["key"]: a for a in founder_client.get(f"{API}/agents").json()["items"]}
    p = make_project(founder_client)
    t = founder_client.post(f"{API}/projects/{p['id']}/tasks", json={"title": "Write landing page copy"}).json()
    assert founder_client.post(f"{API}/projects/{p['id']}/tasks/{t['id']}/run-agent").status_code == 400
    founder_client.patch(f"{API}/projects/{p['id']}/tasks/{t['id']}", json={"assignee_agent_id": agents["funnel"]["id"]})
    r = founder_client.post(f"{API}/projects/{p['id']}/tasks/{t['id']}/run-agent")
    assert r.status_code == 202
    ai_task = founder_client.get(f"{API}/tasks/{r.json()['task_id']}").json()
    assert ai_task["project_id"] == p["id"] and ai_task["agent"]["key"] == "funnel"
    assert ai_task["input"]["context"]["project"]["name"] == "Website rebuild"
    assert founder_client.get(f"{API}/projects/{p['id']}").json()["agent_tasks"][0]["id"] == ai_task["id"]


def test_client_success_agent_sees_real_projects(founder_client: TestClient):
    make_project(founder_client, status="IN_PROGRESS")
    created = founder_client.post(f"{API}/ceo/messages", json={"content": "Give me the status of all active projects."}).json()
    root = founder_client.get(f"{API}/tasks/{created['task_id']}").json()
    child = next(c for c in root["children"] if c["agent"]["key"] == "client_success")
    out = founder_client.get(f"{API}/tasks/{child['id']}").json()["output"]["result"]
    text = " ".join(d["content"] for d in out["deliverables"])
    assert "Website rebuild" in text and "Bright Plumbing" in text


def test_approval_detail_endpoint(founder_client: TestClient):
    founder_client.post(f"{API}/ceo/messages", json={"content": "Prepare a cold email sequence"})
    a = founder_client.get(f"{API}/approvals").json()["items"][0]
    assert founder_client.get(f"{API}/approvals/{a['id']}").json()["title"] == a["title"]
    assert founder_client.get(f"{API}/approvals/9999").status_code == 404
