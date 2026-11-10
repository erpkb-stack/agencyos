"""Phase 10: approved email sending. A fake transport stands in for SMTP; nothing leaves the machine."""

from __future__ import annotations

import smtplib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import get_settings
from app.integrations import email_transport as et
from app.integrations.email_transport import (
    OutgoingEmail,
    RecipientRejected,
    TransientSendError,
    TransportUnavailable,
    get_email_transport,
)
from app.marketing import compliance
from app.models.crm import Prospect, ProspectActivity
from app.models.enums import CampaignStatus, EmailMessageStatus as MS, SalesStage
from app.models.marketing import Campaign, EmailMessage, EmailSuppression
from app.services import email_sending

API = "/api/v1"
BODY = ("Hi {{first_name}},\n\nA thought about {{company_name}}.\n\n{{sender_name}}, {{agency_name}}\n"
        "{{agency_address}}\nUnsubscribe: {{unsubscribe_link}}")


class FakeTransport:
    def __init__(self, fail: dict[str, Exception] | None = None):
        self.sent: list[OutgoingEmail] = []
        self.fail = fail or {}

    def send(self, email: OutgoingEmail) -> str:
        if email.to_email in self.fail:
            raise self.fail[email.to_email]
        self.sent.append(email)
        return f"<{len(self.sent)}@test>"


def _settings(**kw):
    base = dict(agency_name="Test Agency", agency_address="1 Main St, Austin, TX 78701", sender_name="Sam Sender",
                sender_email="sam@agency.example", email_transport="smtp", smtp_host="smtp.example",
                email_send_interval_seconds=0)
    return get_settings().model_copy(update={**base, **kw})


@pytest.fixture
def setup(founder_client: TestClient):
    settings = _settings()
    transport = FakeTransport()
    founder_client.app.dependency_overrides[get_settings] = lambda: settings
    founder_client.app.dependency_overrides[get_email_transport] = lambda: transport
    return founder_client, settings, transport


def _campaign(c: TestClient, *, prospects=(("Alpha", "owner@alpha.com", "Pat Lee"),
                                           ("Bravo", "info@bravo.com", "Kim Ray"),
                                           ("Charlie", "hi@charlie.com", None)),
              steps=((0, "Hello {{first_name}}"), (3, "Following up, {{first_name}}"))) -> dict:
    for name, email, contact in prospects:
        r = c.post(f"{API}/prospects", json={"company_name": name, "email": email, "contact_name": contact,
                                             "industry": "HVAC", "stage": "QUALIFIED", "website": f"{name}.com"})
        assert r.status_code == 201, r.text
    camp = c.post(f"{API}/campaigns", json={"name": "Spring outreach", "campaign_type": "COLD_EMAIL",
                                            "objectives": "Book audits"}).json()
    for i, (delay, subject) in enumerate(steps, start=1):
        r = c.post(f"{API}/emails/templates", json={"name": f"Step {i}", "subject": subject, "body_text": BODY,
                                                    "campaign_id": camp["id"], "kind": "SEQUENCE_STEP",
                                                    "delay_days": delay})
        assert r.status_code == 201, r.text
    c.put(f"{API}/campaigns/{camp['id']}/audience", json={"industry": "hvac"})
    return camp


def _request(c: TestClient, camp: dict) -> int:
    r = c.post(f"{API}/campaigns/{camp['id']}/request-approval",
               json={"consent_basis": "b2b_cold_outreach", "confirm_compliance": True})
    assert r.status_code == 200, r.text
    return r.json()["approval_id"]


def _approve_and_start(c: TestClient, camp: dict, decision="APPROVED") -> None:
    approval_id = _request(c, camp)
    assert c.post(f"{API}/approvals/{approval_id}/decision", json={"decision": decision}).status_code == 200
    if decision == "APPROVED":
        assert c.post(f"{API}/campaigns/{camp['id']}/status", json={"status": "ACTIVE"}).status_code == 200


def _run(session_factory, settings, transport, n=50):
    return email_sending.run_once(session_factory, settings, transport, max_messages=n, sleep=lambda s: None)


def _messages(db, **where):
    db.expire_all()
    q = select(EmailMessage).order_by(EmailMessage.id)
    for k, v in where.items():
        q = q.where(getattr(EmailMessage, k) == v)
    return list(db.scalars(q))


# -- approval freezes exactly what will be sent ------------------------------------------------


def test_approval_request_renders_and_freezes_every_message(setup, db, session_factory):
    c, settings, transport = setup
    camp = _campaign(c)
    approval_id = _request(c, camp)
    msgs = _messages(db)
    # Charlie has no contact name, so {{first_name}} can't be filled: left out rather than sent broken.
    assert {m.to_email for m in msgs} == {"owner@alpha.com", "info@bravo.com"}
    assert len(msgs) == 4 and all(m.status == MS.PENDING_APPROVAL and m.approval_id == approval_id for m in msgs)
    first = next(m for m in msgs if m.to_email == "owner@alpha.com" and m.step == 1)
    assert first.subject == "Hello Pat" and "Test Agency" in first.body_text and "[missing" not in first.body_text
    # localhost PUBLIC_BASE_URL: recipients can't reach a link, so opt-out is by reply.
    assert "mailto:sam@agency.example?subject=Unsubscribe" in first.body_text
    details = c.get(f"{API}/approvals/{approval_id}").json()["payload"]["details"]
    assert "Send 4 emails to 2 recipients (2 steps each" in details and "Charlie: missing first_name" in details

    outbox = c.get(f"{API}/campaigns/{camp['id']}/outbox").json()
    assert outbox["total"] == 4 and outbox["counts"] == {"PENDING_APPROVAL": 4}
    assert outbox["items"][0]["company_name"] in {"Alpha", "Bravo"}
    assert _run(session_factory, settings, transport)["sent"] == 0  # nothing sendable before approval


def test_nothing_is_sent_until_approved_and_started(setup, db, session_factory):
    c, settings, transport = setup
    camp = _campaign(c)
    approval_id = _request(c, camp)
    c.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"})
    assert {m.status for m in _messages(db)} == {MS.SCHEDULED}
    assert _run(session_factory, settings, transport)["sent"] == 0  # approved but campaign not started
    assert c.get(f"{API}/campaigns/{camp['id']}").json()["status"] == "APPROVED"

    c.post(f"{API}/campaigns/{camp['id']}/status", json={"status": "ACTIVE"})
    stats = _run(session_factory, settings, transport)
    assert stats["sent"] == 2 and {e.to_email for e in transport.sent} == {"owner@alpha.com", "info@bravo.com"}
    step2 = _messages(db, step=2)
    assert all(m.status == MS.SCHEDULED and m.send_after is not None for m in step2)
    sent1 = _messages(db, step=1)[0]
    assert abs((step2[0].send_after - sent1.sent_at) - timedelta(days=3)) < timedelta(seconds=5)
    assert _run(session_factory, settings, transport)["sent"] == 0  # follow-ups wait 3 days

    alpha = db.scalar(select(Prospect).where(Prospect.email == "owner@alpha.com"))
    assert alpha.stage == SalesStage.CONTACTED and alpha.last_contacted_at is not None
    assert db.scalar(select(ProspectActivity).where(ProspectActivity.activity_type == "email_sent"))


def test_rejection_cancels_and_edit_after_approval_cancels(setup, db):
    c, _, _ = setup
    camp = _campaign(c)
    _approve_and_start(c, camp, decision="REJECTED")
    assert {m.status for m in _messages(db)} == {MS.CANCELLED}

    approval_id = _request(c, camp)
    c.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"})
    template_id = c.get(f"{API}/emails/templates?campaign_id={camp['id']}").json()["items"][0]["id"]
    c.patch(f"{API}/emails/templates/{template_id}", json={"subject": "Changed {{first_name}}"})
    assert c.get(f"{API}/campaigns/{camp['id']}").json()["status"] == "DRAFT"
    assert not _messages(db, status=MS.SCHEDULED)  # the approved copies can no longer be sent


def test_follow_ups_due_later_and_stop_rules(setup, db, session_factory):
    c, settings, transport = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"), (0, "Next {{first_name}}"), (0, "Last {{first_name}}")))
    _approve_and_start(c, camp)
    _run(session_factory, settings, transport, n=2)  # step 1 to both
    # Bravo replied (stage moved on) and Alpha unsubscribed after approval.
    bravo = db.scalar(select(Prospect).where(Prospect.email == "info@bravo.com"))
    bravo.stage = SalesStage.RESPONDED
    db.commit()
    token = compliance.unsubscribe_token("owner@alpha.com", settings.secret_key)
    assert c.post(f"/unsubscribe?e=owner@alpha.com&t={token}",  # RFC 8058 one-click: e/t in the URL
                  data={"List-Unsubscribe": "One-Click"}).status_code == 200
    stats = _run(session_factory, settings, transport)
    assert stats["sent"] == 0 and stats["skipped"] == 2
    statuses = {(m.to_email, m.step): (m.status, m.error) for m in _messages(db)}
    assert statuses[("owner@alpha.com", 2)][0] == MS.SKIPPED and "do-not-contact" in statuses[("owner@alpha.com", 2)][1]
    assert statuses[("owner@alpha.com", 3)][0] == MS.SKIPPED
    assert "responded" in statuses[("info@bravo.com", 2)][1]
    db.expire_all()
    assert db.get(Campaign, camp["id"]).status == CampaignStatus.COMPLETED  # nothing left to send


def test_pause_cancel_and_daily_limit(setup, db, session_factory):
    c, settings, transport = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"),))
    _approve_and_start(c, camp)
    c.post(f"{API}/campaigns/{camp['id']}/status", json={"status": "PAUSED"})
    assert _run(session_factory, settings, transport)["sent"] == 0
    c.post(f"{API}/campaigns/{camp['id']}/status", json={"status": "ACTIVE"})
    assert _run(session_factory, settings.model_copy(update={"email_daily_limit": 1}), transport)["sent"] == 1
    assert email_sending._state["last_error"].startswith("Daily limit")
    c.post(f"{API}/campaigns/{camp['id']}/status", json={"status": "CANCELLED"})
    assert [m.status for m in _messages(db)].count(MS.CANCELLED) == 1


def test_errors_retry_bounce_and_unavailable(setup, db, session_factory):
    c, settings, _ = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"), (1, "Again {{first_name}}")))
    _approve_and_start(c, camp)

    down = FakeTransport(fail={"owner@alpha.com": TransportUnavailable("The SMTP server refused the login (535)"),
                               "info@bravo.com": TransportUnavailable("x")})
    assert _run(session_factory, settings, down) == {"sent": 0, "failed": 0, "skipped": 0, "retry": 0}
    assert all(m.status == MS.SCHEDULED and m.attempts == 0 for m in _messages(db, step=1))  # untouched
    assert "refused the login" in c.get(f"{API}/emails/sending").json()["last_error"]

    flaky = FakeTransport(fail={"owner@alpha.com": TransientSendError("Server asked to retry later (451)"),
                                "info@bravo.com": RecipientRejected("Recipient address rejected (550)")})
    stats = _run(session_factory, settings, flaky)
    assert stats == {"sent": 0, "failed": 1, "skipped": 0, "retry": 1}
    alpha = _messages(db, to_email="owner@alpha.com", step=1)[0]
    assert alpha.status == MS.SCHEDULED and alpha.attempts == 1 and alpha.send_after > alpha.updated_at
    assert _messages(db, to_email="info@bravo.com", step=2)[0].status == MS.SKIPPED  # no follow-up after bounce
    assert db.scalar(select(EmailSuppression).where(EmailSuppression.email == "info@bravo.com")).reason.value == "BOUNCED"

    # Retries give up after the last delay.
    alpha.attempts, alpha.send_after = 3, alpha.send_after - timedelta(days=1)
    db.commit()
    assert _run(session_factory, settings, flaky)["failed"] == 1
    assert "Gave up after 4 attempts" in _messages(db, to_email="owner@alpha.com", step=1)[0].error


def test_redirect_headers_and_transport_off(setup, db, session_factory):
    c, settings, transport = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"),))
    _approve_and_start(c, camp)
    assert _run(session_factory, settings, None)["sent"] == 0  # EMAIL_TRANSPORT=disabled
    public = settings.model_copy(update={"email_redirect_to": "me@agency.example",
                                         "public_base_url": "https://os.agency.example"})
    _run(session_factory, public, transport)
    assert {e.to_email for e in transport.sent} == {"me@agency.example"}
    first = transport.sent[0]
    assert first.subject.startswith("[Test → ") and first.unsubscribe_url.startswith("https://os.agency.example/unsubscribe?")
    mime = et.build_mime(public, first)
    assert mime["From"] == "Sam Sender <sam@agency.example>" and mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "mailto:sam@agency.example" in mime["List-Unsubscribe"] and mime["Message-ID"].endswith("@agency.example>")


def test_interrupted_send_is_not_resent(setup, db, session_factory):
    c, _, _ = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"),))
    _approve_and_start(c, camp)
    m = _messages(db)[0]
    m.status = MS.SENDING
    db.commit()
    with session_factory() as s:
        assert email_sending.recover_interrupted(s) == 1
    assert "may or may not have been delivered" in _messages(db)[0].error


def test_test_send_goes_only_to_the_signed_in_user(setup, db):
    c, settings, transport = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"),))
    template_id = c.get(f"{API}/emails/templates?campaign_id={camp['id']}").json()["items"][0]["id"]
    alpha = db.scalar(select(Prospect).where(Prospect.email == "owner@alpha.com"))
    r = c.post(f"{API}/emails/templates/{template_id}/test-send", json={"prospect_id": alpha.id})
    assert r.status_code == 200 and r.json()["sent_to"] == "founder@example.com"
    assert transport.sent[-1].to_email == "founder@example.com" and transport.sent[-1].subject == "[Test] Hello Pat"
    assert c.post(f"{API}/emails/sending/test").json()["sent_to"] == "founder@example.com"
    status = c.get(f"{API}/emails/sending").json()
    assert status["enabled"] and status["unsubscribe_mode"] == "reply" and status["from"] == "sam@agency.example"
    assert "password" not in str(status).lower()

    c.app.dependency_overrides[get_email_transport] = lambda: None
    off = c.post(f"{API}/emails/sending/test")
    assert off.status_code == 400 and "turned off" in off.json()["error"]["message"]


def test_staff_cannot_approve_and_agents_cannot_send(setup, db, make_user):
    from tests.conftest import login

    c, _, _ = setup
    camp = _campaign(c, steps=((0, "Hello {{first_name}}"),))
    approval_id = _request(c, camp)
    from app.models.enums import UserRole

    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    staff = login(TestClient(c.app), "staff@example.com")
    assert staff.post(f"{API}/approvals/{approval_id}/decision", json={"decision": "APPROVED"}).status_code == 403
    assert {m.status for m in _messages(db)} == {MS.PENDING_APPROVAL}


# -- SMTP transport (smtplib replaced; no network) ---------------------------------------------


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    error: Exception | None = None
    login_error: Exception | None = None

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.calls = host, port, []
        _FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        if _FakeSMTP.login_error:
            raise _FakeSMTP.login_error
        self.calls.append(("login", user))

    def send_message(self, msg):
        if _FakeSMTP.error:
            raise _FakeSMTP.error
        self.calls.append(("send", msg["To"]))

    def quit(self):
        self.calls.append("quit")


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances, _FakeSMTP.error, _FakeSMTP.login_error = [], None, None
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_smtp_transport_classifies_errors(fake_smtp):
    s = _settings(smtp_username="user", smtp_password="pw")
    t = et.SmtpTransport(s)
    email = OutgoingEmail("a@b.com", "Hi", "Body", None, "mailto:sam@agency.example")
    assert t.send(email).endswith("@agency.example>")
    assert fake_smtp.instances[-1].calls == ["starttls", ("login", "user"), ("send", "a@b.com"), "quit"]

    fake_smtp.error = smtplib.SMTPRecipientsRefused({"a@b.com": (550, b"no such user")})
    with pytest.raises(RecipientRejected):
        t.send(email)
    fake_smtp.error = smtplib.SMTPRecipientsRefused({"a@b.com": (452, b"mailbox full")})
    with pytest.raises(TransientSendError):
        t.send(email)
    fake_smtp.error = smtplib.SMTPServerDisconnected("gone")
    with pytest.raises(TransientSendError):
        t.send(email)
    fake_smtp.error = None
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with pytest.raises(TransportUnavailable) as err:
        t.send(email)
    assert "535" in str(err.value) and "pw" not in str(err.value)
    with pytest.raises(TransportUnavailable):
        et.SmtpTransport(_settings(smtp_host="")).send(email)
    assert et.default_transport(_settings(email_transport="disabled")) is None


def test_settings_warnings_for_smtp():
    warnings = _settings(smtp_host="", smtp_security="none", smtp_username="u").production_warnings()
    assert any("SMTP_HOST" in w for w in warnings) and any("unencrypted" in w for w in warnings)
