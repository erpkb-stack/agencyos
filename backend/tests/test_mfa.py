"""Phase 9: two-factor authentication and session management."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cli
from app.config.settings import get_settings
from app.models.enums import UserRole
from app.models.identity import AuditLog, User
from app.security import mfa
from tests.conftest import PASSWORD, login

API = "/api/v1"


# -- algorithm ---------------------------------------------------------------------------------


def test_totp_matches_rfc6238_test_vectors():
    # RFC 6238 Appendix B, SHA-1 secret "12345678901234567890" (8 digits there; we use the last 6).
    secret = base64.b32encode(b"12345678901234567890").decode()
    for t, expected8 in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")]:
        assert mfa.hotp(mfa._key(secret), t // 30, digits=8) == expected8
        assert mfa.code_at(secret, t // 30) == expected8[-6:]


def test_verify_window_and_replay():
    secret = mfa.new_secret()
    now = 1_700_000_000
    step = mfa.current_step(now)
    assert mfa.verify(secret, mfa.code_at(secret, step), last_step=None, now=now) == step
    assert mfa.verify(secret, mfa.code_at(secret, step - 1), last_step=None, now=now) == step - 1  # drift ok
    assert mfa.verify(secret, mfa.code_at(secret, step - 3), last_step=None, now=now) is None  # too old
    assert mfa.verify(secret, mfa.code_at(secret, step), last_step=step, now=now) is None  # replay refused
    assert mfa.verify(secret, "12 34", last_step=None, now=now) is None


def test_secret_encryption_and_tokens():
    enc = mfa.encrypt_secret("JBSWY3DPEHPK3PXP", "k" * 40)
    assert "JBSWY3DP" not in enc
    assert mfa.decrypt_secret(enc, "k" * 40) == "JBSWY3DPEHPK3PXP"
    assert mfa.decrypt_secret(enc, "other-key" * 5) is None
    token = mfa.issue_mfa_token(7, "hash", "k" * 40, now=1000)
    assert mfa.read_mfa_token(token, "k" * 40, now=1100) == (7, mfa.password_fingerprint("hash"))
    assert mfa.read_mfa_token(token, "k" * 40, now=1000 + mfa.MFA_TOKEN_TTL + 1) is None  # expired
    assert mfa.read_mfa_token(token.replace("7.", "8.", 1), "k" * 40, now=1100) is None  # tampered
    codes = mfa.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    stored = [mfa.hash_recovery_code(c) for c in codes]
    assert mfa.use_recovery_code(stored, codes[3].upper()) == stored[:3] + stored[4:]
    assert mfa.use_recovery_code(stored, "nope-nope") is None
    assert mfa.qr_svg_data_uri("otpauth://totp/x").startswith("data:image/svg+xml;base64,")


# -- flows -------------------------------------------------------------------------------------


def _current_code(db, email="founder@example.com") -> str:
    db.expire_all()
    user = db.scalar(select(User).where(User.email == email))
    secret = mfa.decrypt_secret(user.totp_secret, get_settings().secret_key)
    step = mfa.current_step() + 1 if user.totp_last_step == mfa.current_step() else mfa.current_step()
    return mfa.code_at(secret, step)  # next step if the current one is used; inside the ±1 window


def enrol(c: TestClient, db) -> list[str]:
    setup = c.post(f"{API}/auth/2fa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/AgencyOS:") and setup["qr_svg"].startswith("data:image/svg")
    assert c.post(f"{API}/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    r = c.post(f"{API}/auth/2fa/enable", json={"code": mfa.code_at(setup["secret"], mfa.current_step())})
    assert r.status_code == 200, r.text
    return r.json()["recovery_codes"]


def test_enrol_and_two_step_login(founder_client: TestClient, db):
    codes = enrol(founder_client, db)
    assert len(codes) == 10
    user = db.scalar(select(User))
    db.refresh(user)
    assert user.totp_enabled and codes[0] not in str(user.recovery_codes)  # stored hashed
    assert founder_client.get(f"{API}/auth/2fa").json() == {
        **founder_client.get(f"{API}/auth/2fa").json(), "enabled": True, "recovery_codes_left": 10}

    fresh = TestClient(founder_client.app)
    step1 = fresh.post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD}).json()
    assert step1["mfa_required"] is True and "user" not in step1
    assert fresh.get(f"{API}/auth/me").status_code == 401  # no session after the password alone
    bad = fresh.post(f"{API}/auth/login/2fa", json={"mfa_token": step1["mfa_token"], "code": "123456"})
    assert bad.status_code == 401
    ok = fresh.post(f"{API}/auth/login/2fa", json={"mfa_token": step1["mfa_token"], "code": _current_code(db)})
    assert ok.status_code == 200 and ok.json()["user"]["totp_enabled"] is True
    assert fresh.get(f"{API}/auth/me").status_code == 200
    assert db.scalar(select(AuditLog).where(AuditLog.summary.contains("authenticator code")))


def test_code_cannot_be_reused_and_recovery_codes_are_single_use(founder_client: TestClient, db):
    codes = enrol(founder_client, db)
    used = _current_code(db)

    def step1(c):
        return c.post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD}).json()["mfa_token"]

    a = TestClient(founder_client.app)
    assert a.post(f"{API}/auth/login/2fa", json={"mfa_token": step1(a), "code": used}).status_code == 200
    b = TestClient(founder_client.app)
    assert b.post(f"{API}/auth/login/2fa", json={"mfa_token": step1(b), "code": used}).status_code == 401  # replay

    c1 = TestClient(founder_client.app)
    assert c1.post(f"{API}/auth/login/2fa", json={"mfa_token": step1(c1), "recovery_code": codes[0]}).status_code == 200
    c2 = TestClient(founder_client.app)
    assert c2.post(f"{API}/auth/login/2fa", json={"mfa_token": step1(c2), "recovery_code": codes[0]}).status_code == 401
    assert founder_client.get(f"{API}/auth/2fa").json()["recovery_codes_left"] == 9


def test_wrong_codes_throttle_the_account(founder_client: TestClient, db):
    enrol(founder_client, db)
    c = TestClient(founder_client.app)
    token = c.post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD}).json()["mfa_token"]
    statuses = [c.post(f"{API}/auth/login/2fa", json={"mfa_token": token, "code": "000000"}).status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]


def test_password_change_invalidates_pending_login(founder_client: TestClient, db):
    enrol(founder_client, db)
    c = TestClient(founder_client.app)
    token = c.post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD}).json()["mfa_token"]
    founder_client.post(f"{API}/auth/change-password", json={"current_password": PASSWORD, "new_password": "Brand-New-Pass-77"})
    r = c.post(f"{API}/auth/login/2fa", json={"mfa_token": token, "code": _current_code(db)})
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]


def test_disable_needs_password_and_code(founder_client: TestClient, db):
    codes = enrol(founder_client, db)
    assert founder_client.post(f"{API}/auth/2fa/disable", json={"password": "wrong", "code": _current_code(db)}).status_code == 401
    assert founder_client.post(f"{API}/auth/2fa/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 401
    regenerated = founder_client.post(f"{API}/auth/2fa/recovery-codes", json={"code": _current_code(db)}).json()["recovery_codes"]
    assert set(regenerated).isdisjoint(codes)
    assert founder_client.post(f"{API}/auth/2fa/disable", json={"password": PASSWORD, "recovery_code": codes[1]}).status_code == 401  # old codes void
    assert founder_client.post(f"{API}/auth/2fa/disable", json={"password": PASSWORD, "recovery_code": regenerated[0]}).status_code == 204
    step1 = TestClient(founder_client.app).post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD}).json()
    assert step1["mfa_required"] is False


def test_require_2fa_policy_for_founders_and_admins(client: TestClient, make_user, db):
    make_user()
    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    strict = get_settings().model_copy(update={"require_2fa_for_admins": True})
    client.app.dependency_overrides[get_settings] = lambda: strict
    founder = login(TestClient(client.app), "founder@example.com")
    r = founder.get(f"{API}/prospects")
    assert r.status_code == 403 and r.json()["error"]["code"] == "mfa_setup_required"
    assert founder.get(f"{API}/auth/me").status_code == 200
    assert founder.get(f"{API}/auth/2fa").json()["required"] is True
    staff = login(TestClient(client.app), "staff@example.com")
    assert staff.get(f"{API}/prospects").status_code == 200  # policy only covers Founders/Admins
    enrol(founder, db)
    assert founder.get(f"{API}/prospects").status_code == 200


def test_sessions_list_and_revoke(client: TestClient, make_user):
    make_user()
    phone = login(TestClient(client.app), "founder@example.com")
    laptop = login(TestClient(client.app), "founder@example.com")
    items = laptop.get(f"{API}/auth/sessions").json()["items"]
    assert len(items) == 2 and sum(i["current"] for i in items) == 1
    other = next(i for i in items if not i["current"])
    current = next(i for i in items if i["current"])
    assert laptop.delete(f"{API}/auth/sessions/{current['id']}").status_code == 400
    assert laptop.delete(f"{API}/auth/sessions/{other['id']}").status_code == 204
    assert phone.get(f"{API}/auth/me").status_code == 401
    third = login(TestClient(client.app), "founder@example.com")
    assert laptop.post(f"{API}/auth/sessions/revoke-others").json()["revoked"] == 1
    assert third.get(f"{API}/auth/me").status_code == 401 and laptop.get(f"{API}/auth/me").status_code == 200


def test_cli_reset_2fa(founder_client: TestClient, db, session_factory, monkeypatch, capsys):
    enrol(founder_client, db)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    assert cli.main(["reset-2fa", "--email", "founder@example.com"]) == 0
    assert cli.main(["reset-2fa", "--email", "nobody@example.com"]) == 1
    db.expire_all()
    user = db.scalar(select(User))
    assert not user.totp_enabled and user.totp_secret is None
    assert founder_client.get(f"{API}/auth/me").status_code == 401  # signed out everywhere
    assert "removed" in capsys.readouterr().out


def test_setup_requires_a_session(client: TestClient):
    assert client.post(f"{API}/auth/2fa/setup").status_code == 401
    assert client.post(f"{API}/auth/login/2fa", json={"mfa_token": "1.2.3.4.5abcdef", "code": "123456"}).status_code == 401
