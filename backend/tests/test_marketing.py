"""Phase 4: campaigns, email drafts and compliance, suppression/unsubscribe, funnels and landing pages."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config.settings import get_settings
from app.marketing import compliance
from app.models.approvals import Approval
from app.models.enums import UserRole
from tests.conftest import login

API = "/api/v1"
GOOD_BODY = ("Hi {{first_name}},\n\nQuick note about {{company_name}}.\n\n—\n{{sender_name}}, {{agency_name}}\n"
             "{{agency_address}}\nUnsubscribe: {{unsubscribe_link}}")


# -- compliance (unit) -------------------------------------------------------------------------


def test_compliance_rules():
    bad = compliance.check_email("", "Buy now. 100% free!")
    codes = {i["code"] for i in bad if i["level"] == "error"}
    assert {"subject_missing", "no_unsubscribe", "no_sender", "no_postal_address"} <= codes
    assert any(i["code"] == "spam_phrase" for i in bad)

    good = compliance.check_email("Quick question for {{company_name}}", GOOD_BODY)
    assert not compliance.has_errors(good)
    warn = compliance.check_email("RE: hello {{favourite_colour}}", GOOD_BODY)
    assert {i["code"] for i in warn} == {"reply_prefix", "unknown_merge_fields"}


def test_render_never_invents_values():
    text, missing = compliance.render("Hi {{first_name}} at {{company_name}}", {"company_name": "Acme"})
    assert text == "Hi [missing: first_name] at Acme" and missing == ["first_name"]
    token = compliance.unsubscribe_token("A@B.com", "secret")
    assert compliance.unsubscribe_valid("a@b.com", token, "secret")
    assert not compliance.unsubscribe_valid("other@b.com", token, "secret")


# -- campaigns ---------------------------------------------------------------------------------


def seed_prospects(c: TestClient) -> None:
    for i, email in enumerate(["owner@alpha.com", "info@bravo.com", "hi@charlie.com"]):
        r = c.post(f"{API}/prospects", json={"company_name": f"Prospect {i}", "industry": "HVAC", "city": "Austin",
                                             "email": email, "contact_name": "Pat Lee", "stage": "QUALIFIED",
                                             "website": f"p{i}.com"})
        assert r.status_code == 201, r.text
    c.post(f"{API}/emails/suppressions", json={"email": "info@bravo.com", "reason": "UNSUBSCRIBED"})


def make_campaign(c: TestClient, **kw) -> dict:
    r = c.post(f"{API}/campaigns", json={"name": "HVAC outreach", "campaign_type": "COLD_EMAIL",
                                         "objectives": "Book 10 audits", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_email_campaign_end_to_end(founder_client: TestClient, db):
    seed_prospects(founder_client)
    camp = make_campaign(founder_client)
    base = f"{API}/campaigns/{camp['id']}"
    not_ready = founder_client.post(f"{base}/request-approval", json={})
    assert not_ready.status_code == 400
    assert any("email draft" in d for d in not_ready.json()["error"]["details"])

    # AI drafts: the email agent's own "send" proposal is NOT queued (draft-only task).
    approvals_before = db.scalar(select(func.count()).select_from(Approval))
    task_id = founder_client.post(f"{base}/generate", json={"kind": "emails"}).json()["task_id"]
    task = founder_client.get(f"{API}/tasks/{task_id}").json()
    assert task["status"] == "COMPLETED"
    assert db.scalar(select(func.count()).select_from(Approval)) == approvals_before
    assert any("not queued" in n for n in task["output"]["notes"])

    imported = founder_client.post(f"{base}/import/{task_id}", json={}).json()
    assert imported["emails_created"] == 3
    emails = imported["campaign"]["emails"]
    assert [e["sequence_order"] for e in emails] == [1, 2, 3]
    assert emails[0]["subject"].startswith("Quick question") and emails[0]["compliant"]

    seg = founder_client.put(f"{base}/audience", json={"stages": ["QUALIFIED"], "industry": "hvac"}).json()
    assert seg == {**seg, "matched": 3, "with_email": 3, "suppressed": 1, "eligible": 2}

    no_consent = founder_client.post(f"{base}/request-approval", json={})
    assert no_consent.status_code == 400 and "consent" in no_consent.json()["error"]["message"]
    r = founder_client.post(f"{base}/request-approval",
                            json={"consent_basis": "b2b_cold_outreach", "confirm_compliance": True})
    assert r.status_code == 200
    camp = r.json()["campaign"]
    assert camp["status"] == "AWAITING_APPROVAL" and all(e["status"] == "AWAITING_APPROVAL" for e in camp["emails"])
    approval = founder_client.get(f"{API}/approvals/{r.json()['approval_id']}").json()
    assert approval["action_type"] == "SEND_EMAIL" and approval["entity_type"] == "campaign"
    assert "2 eligible recipients (1 suppressed address excluded); 3 emails" in approval["payload"]["details"]

    locked = founder_client.patch(f"{API}/emails/templates/{emails[0]['id']}", json={"subject": "New subject"})
    assert locked.status_code == 400
    assert founder_client.post(f"{base}/request-approval", json={}).status_code == 400  # no double request

    d = founder_client.post(f"{API}/approvals/{approval['id']}/decision", json={"decision": "APPROVED"}).json()
    assert d["executed"] is False
    camp = founder_client.get(base).json()
    assert camp["status"] == "APPROVED" and all(e["status"] == "APPROVED" for e in camp["emails"])
    assert camp["allowed_status_changes"] == ["ACTIVE", "CANCELLED"]

    # Editing after approval sends it back to draft.
    founder_client.patch(f"{API}/emails/templates/{emails[0]['id']}", json={"subject": "Updated subject"})
    assert founder_client.get(base).json()["status"] == "DRAFT"


def test_status_budget_change_and_metrics(founder_client: TestClient):
    camp = make_campaign(founder_client, name="Search ads", campaign_type="GOOGLE_ADS")
    base = f"{API}/campaigns/{camp['id']}"
    r = founder_client.post(f"{base}/request-approval", json={})
    assert r.status_code == 400 and any("budget" in d for d in r.json()["error"]["details"])
    founder_client.patch(base, json={"budget": "500"})
    approval_id = founder_client.post(f"{base}/request-approval", json={}).json()["approval_id"]
    assert founder_client.get(f"{API}/approvals/{approval_id}").json()["action_type"] == "LAUNCH_ADS"
    assert founder_client.post(f"{base}/status", json={"status": "ACTIVE"}).status_code == 400  # not approved yet
    founder_client.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"})
    active = founder_client.post(f"{base}/status", json={"status": "ACTIVE"}).json()
    assert active["status"] == "ACTIVE"

    assert founder_client.patch(base, json={"name": "Renamed"}).status_code == 400
    r = founder_client.patch(base, json={"budget": "900"}).json()
    assert r["status"] == "AWAITING_APPROVAL" and r["budget"] == "900.00"
    change = next(a for a in r["approvals"] if a["action_type"] == "CHANGE_BUDGET")
    founder_client.post(f"{API}/approvals/{change['id']}/decision", json={"decision": "REJECTED"})
    back = founder_client.get(base).json()
    assert back["status"] == "ACTIVE" and back["budget"] == "500.00"

    m = founder_client.put(f"{base}/metrics", json={"impressions": 10000, "clicks": 250, "leads": 20,
                                                    "conversions": 5, "spend": "400"}).json()
    assert m["derived_metrics"]["ctr"] == 2.5
    assert m["derived_metrics"]["cost_per_lead"] == "20.00"
    assert m["derived_metrics"]["conversion_rate"] == 25.0
    task_id = founder_client.post(f"{base}/analyze").json()["task_id"]
    assert founder_client.get(f"{API}/tasks/{task_id}").json()["agent"]["key"] == "analytics"


def test_staff_requests_but_cannot_approve_and_admin_cannot_self_approve(client: TestClient, make_user):
    make_user()
    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    make_user(email="admin@example.com", role=UserRole.ADMIN, name="Ada Admin")
    for email in ("staff@example.com", "admin@example.com"):
        c = login(TestClient(client.app), email)
        camp = make_campaign(c, name=f"Ads {email}", campaign_type="META_ADS", budget="100")
        approval_id = c.post(f"{API}/campaigns/{camp['id']}/request-approval", json={}).json()["approval_id"]
        assert c.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"}).status_code == 403
    founder = login(TestClient(client.app), "founder@example.com")
    pending = founder.get(f"{API}/approvals?status=PENDING").json()["items"]
    assert len(pending) == 2
    assert all(p["requested_by_user"] in ("Sam Staff", "Ada Admin") for p in pending)


# -- email library, preview, suppression, unsubscribe ------------------------------------------


def test_email_templates_preview_and_check(founder_client: TestClient):
    r = founder_client.post(f"{API}/emails/templates", json={"name": "Intro", "subject": "Hello {{first_name}}",
                                                             "body_text": GOOD_BODY})
    t = r.json()
    assert t["kind"] == "TEMPLATE" and t["compliant"]
    bad = founder_client.post(f"{API}/emails/templates", json={"name": "Bad", "subject": "Hi", "body_text": "Hi there"}).json()
    assert not bad["compliant"]

    p = founder_client.post(f"{API}/prospects", json={"company_name": "Cool Air", "email": "ana@coolair.com",
                                                      "contact_name": "Ana Ruiz"}).json()
    prev = founder_client.get(f"{API}/emails/templates/{t['id']}/preview?prospect_id={p['id']}").json()
    assert prev["subject"] == "Hello Ana"
    assert "Cool Air" in prev["body_text"]
    assert "/unsubscribe?e=ana%40coolair.com&t=" in prev["body_text"]
    assert set(prev["missing_fields"]) == {"sender_name", "agency_name", "agency_address"}  # not configured here
    check = founder_client.post(f"{API}/emails/check", json={"subject": "x", "body_text": "y"}).json()
    assert check["compliant"] is False


def test_suppression_and_public_unsubscribe(founder_client: TestClient):
    founder_client.post(f"{API}/emails/suppressions", json={"email": "Manual@Example.com"})
    lst = founder_client.get(f"{API}/emails/suppressions").json()["items"]
    assert lst[0]["email"] == "manual@example.com" and lst[0]["reason"] == "MANUAL"

    token = compliance.unsubscribe_token("lead@shop.com", get_settings().secret_key)
    page = founder_client.get(f"/unsubscribe?e=lead@shop.com&t={token}")
    assert page.status_code == 200 and "Stop marketing emails" in page.text
    assert "invalid" in founder_client.get("/unsubscribe?e=lead@shop.com&t=forged").text
    done = founder_client.post("/unsubscribe", data={"e": "lead@shop.com", "t": token})
    assert "unsubscribed" in done.text
    rows = {s["email"]: s for s in founder_client.get(f"{API}/emails/suppressions").json()["items"]}
    assert rows["lead@shop.com"]["reason"] == "UNSUBSCRIBED"
    assert founder_client.delete(f"{API}/emails/suppressions/{rows['lead@shop.com']['id']}").status_code == 403
    assert founder_client.delete(f"{API}/emails/suppressions/{rows['manual@example.com']['id']}").status_code == 204

    check = founder_client.post(f"{API}/emails/recipients/check",
                                json={"emails": ["lead@shop.com", "ok@x.com", "OK@x.com", "nope"]}).json()
    assert check == {"eligible": ["ok@x.com"], "suppressed": ["lead@shop.com"], "invalid": ["nope"],
                     "duplicates": ["ok@x.com"]}


def test_library_generation_and_import(founder_client: TestClient):
    task_id = founder_client.post(f"{API}/emails/generate", json={"purpose": "sequence", "brief": "Roofers"}).json()["task_id"]
    assert founder_client.get(f"{API}/emails/ai-tasks").json()["items"][0]["id"] == task_id
    created = founder_client.post(f"{API}/emails/import/{task_id}").json()["items"]
    assert len(created) == 3 and all(e["kind"] == "TEMPLATE" for e in created)


# -- funnels -----------------------------------------------------------------------------------


def test_funnel_templates_validation_preview_and_publish(founder_client: TestClient):
    assert {t["key"] for t in founder_client.get(f"{API}/funnels/templates").json()["items"]} == {
        "hvac", "plumbing", "roofing", "accounting", "real_estate"}
    f = founder_client.post(f"{API}/funnels", json={"name": "Cool Air leads", "template_key": "hvac"}).json()
    assert f["industry"] == "HVAC" and len(f["lead_fields"]) == 5
    assert not f["ready"] and any("placeholder" in v["message"] for v in f["validation"])
    assert all(c["passed"] for c in f["mobile_checks"])
    base = f"{API}/funnels/{f['id']}"
    assert founder_client.post(f"{base}/request-approval").status_code == 400

    cfg = f["form_config"]
    cfg["copy"].update(proof=None, business_name="Cool Air HVAC", phone_display="(512) 555-0100",
                       subheadline="Local technicians serving Austin.", headline="<script>alert(1)</script> Fast AC repair")
    cfg["consent_text"] = "By submitting, you agree that Cool Air HVAC may contact you about your request."
    r = founder_client.patch(base, json={"form_config": cfg})
    assert r.status_code == 200, r.text
    assert r.json()["ready"], r.json()["validation"]

    preview = founder_client.get(f"{base}/preview")
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "form-action 'none'" in preview.headers["content-security-policy"]
    assert "<script>" not in preview.text and "&lt;script&gt;" in preview.text
    assert 'name="viewport"' in preview.text and "Preview generated by AgencyOS" in preview.text
    download = founder_client.get(f"{base}/download")
    assert "attachment" in download.headers["content-disposition"] and "Preview generated" not in download.text

    approval_id = founder_client.post(f"{base}/request-approval").json()["approval_id"]
    assert founder_client.get(f"{API}/approvals/{approval_id}").json()["action_type"] == "PUBLISH_WEBSITE"
    assert founder_client.patch(base, json={"name": "x"}).status_code == 400
    founder_client.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"})
    assert founder_client.get(base).json()["status"] == "APPROVED"
    pub = founder_client.post(f"{base}/published", json={"landing_page_url": "coolair.com/audit"}).json()
    assert pub["status"] == "PUBLISHED" and pub["landing_page_url"] == "https://coolair.com/audit"
    edited = founder_client.patch(base, json={"call_to_action": "Book now"}).json()
    assert edited["status"] == "DRAFT"


def test_funnel_field_rules(founder_client: TestClient):
    f = founder_client.post(f"{API}/funnels", json={"name": "Custom", "lead_fields": [
        {"name": "notes", "label": "Notes", "type": "textarea"},
        {"name": "notes", "label": "Again", "type": "text"},
        {"name": "service", "label": "Service", "type": "select"}]}).json()
    messages = " | ".join(v["message"] for v in f["validation"])
    assert "used more than once" in messages and "no options" in messages
    assert "email or a phone" in messages
    bad_type = founder_client.post(f"{API}/funnels", json={"name": "X", "lead_fields": [{"name": "a", "label": "A", "type": "file"}]})
    assert bad_type.status_code == 422


def test_funnel_ai_copy_import(founder_client: TestClient):
    f = founder_client.post(f"{API}/funnels", json={"name": "Roof leads", "template_key": "roofing"}).json()
    task_id = founder_client.post(f"{API}/funnels/{f['id']}/generate", json={}).json()["task_id"]
    result = founder_client.post(f"{API}/funnels/{f['id']}/import/{task_id}").json()
    assert set(result["used"]) == {"Headline", "Subheadline", "Benefits", "Proof", "Call to action"}
    copy = result["funnel"]["form_config"]["copy"]
    assert copy["proof"] == "[customer review]"  # placeholder kept, never a fake review
    assert any("[customer review]" in v["message"] for v in result["funnel"]["validation"])
