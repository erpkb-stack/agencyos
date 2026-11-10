from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.config.settings import Settings
from app.models.enums import UserRole
from app.models.identity import AuditLog, User, UserSession
from app.security.deps import login_limiter
from app.security.tokens import CSRF_HEADER, SESSION_COOKIE
from app.utils.time import utcnow
from tests.conftest import PASSWORD, login


def test_login_sets_cookies_and_me_works(client: TestClient, make_user):
    make_user()
    r = client.post("/api/v1/auth/login", json={"email": "Founder@Example.com ", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "founder@example.com"
    assert body["user"]["role"] == "FOUNDER"
    assert "password_hash" not in body["user"]
    assert body["csrf_token"] == client.cookies["agencyos_csrf"]
    set_cookie = r.headers.get_list("set-cookie")
    assert any(c.startswith(f"{SESSION_COOKIE}=") and "HttpOnly" in c for c in set_cookie)

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["full_name"] == "Fran Founder"


def test_session_token_is_stored_hashed(client: TestClient, make_user, db):
    make_user()
    login(client, "founder@example.com")
    raw = client.cookies[SESSION_COOKIE]
    stored = db.scalar(select(UserSession.token_hash))
    assert stored and stored != raw and len(stored) == 64


def test_password_never_stored_in_plaintext(make_user, db):
    make_user()
    user = db.scalar(select(User))
    assert user.password_hash.startswith("scrypt$")
    assert PASSWORD not in user.password_hash


@pytest.mark.parametrize(
    "email,password",
    [("founder@example.com", "wrong-password"), ("nobody@example.com", PASSWORD)],
)
def test_bad_credentials_rejected_uniformly(client: TestClient, make_user, db, email, password):
    make_user()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "unauthenticated"
    assert err["message"] == "Invalid email or password"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.login_failed"))


def test_inactive_user_cannot_login(client: TestClient, make_user):
    make_user(active=False)
    r = client.post("/api/v1/auth/login", json={"email": "founder@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_protected_route_requires_session(client: TestClient):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_logout_revokes_session(founder_client: TestClient):
    token = founder_client.cookies[SESSION_COOKIE]
    assert founder_client.post("/api/v1/auth/logout").status_code == 204
    founder_client.cookies.set(SESSION_COOKIE, token)  # replay the old cookie
    assert founder_client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_rejected(founder_client: TestClient, db):
    session = db.scalar(select(UserSession))
    session.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert founder_client.get("/api/v1/auth/me").status_code == 401


def test_csrf_required_for_writes_with_session(founder_client: TestClient):
    good = founder_client.headers.pop(CSRF_HEADER)
    r = founder_client.post("/api/v1/auth/logout")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"

    r = founder_client.post("/api/v1/auth/logout", headers={CSRF_HEADER: "forged"})
    assert r.status_code == 403

    r = founder_client.post("/api/v1/auth/logout", headers={CSRF_HEADER: good})
    assert r.status_code == 204


def test_change_password_flow(client: TestClient, make_user):
    make_user()
    login(client, "founder@example.com")

    # A second session that must be revoked by the change.
    with TestClient(client.app) as other:
        login(other, "founder@example.com")
        assert other.get("/api/v1/auth/me").status_code == 200

        weak = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "short"},
        )
        assert weak.status_code == 400
        assert weak.json()["error"]["details"]

        wrong = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "nope", "new_password": "New-Password-99"},
        )
        assert wrong.status_code == 401

        ok = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "New-Password-99"},
        )
        assert ok.status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 200  # current session kept
        assert other.get("/api/v1/auth/me").status_code == 401  # other session revoked

    client.cookies.clear()
    bad = client.post("/api/v1/auth/login", json={"email": "founder@example.com", "password": PASSWORD})
    assert bad.status_code == 401
    login(client, "founder@example.com", "New-Password-99")


def test_role_rules_for_user_management(client: TestClient, make_user):
    make_user()
    make_user(email="admin@example.com", role=UserRole.ADMIN, name="Ada Admin")
    make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    make_user(email="ro@example.com", role=UserRole.READ_ONLY, name="Rita Reader")

    new_user = {"email": "x@example.com", "full_name": "X", "password": "Valid-Pass-123"}

    for email in ("staff@example.com", "ro@example.com"):
        c = login(TestClient(client.app), email)
        assert c.get("/api/v1/auth/users").status_code == 403
        assert c.post("/api/v1/auth/users", json={**new_user, "role": "STAFF"}).status_code == 403

    admin = login(TestClient(client.app), "admin@example.com")
    assert admin.get("/api/v1/auth/users").status_code == 200
    assert admin.post("/api/v1/auth/users", json={**new_user, "role": "ADMIN"}).status_code == 403
    created = admin.post("/api/v1/auth/users", json={**new_user, "role": "STAFF"})
    assert created.status_code == 201
    assert created.json()["role"] == "STAFF"

    founder = login(TestClient(client.app), "founder@example.com")
    r = founder.post(
        "/api/v1/auth/users", json={**new_user, "email": "a2@example.com", "role": "ADMIN"}
    )
    assert r.status_code == 201
    dup = founder.post("/api/v1/auth/users", json={**new_user, "role": "STAFF"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"


def test_validation_errors_use_standard_shape(founder_client: TestClient):
    r = founder_client.post(
        "/api/v1/auth/users",
        json={"email": "not-an-email", "full_name": "", "password": "x", "role": "KING"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    fields = {d["field"] for d in body["error"]["details"]}
    assert {"body.email", "body.full_name", "body.role"} <= fields
    assert body["request_id"]


def test_login_rate_limited(client: TestClient, make_user):
    make_user()
    limit = login_limiter.limit
    # Different accounts each time, so only the per-IP limit applies (not the per-account one).
    statuses = [
        client.post("/api/v1/auth/login", json={"email": f"user{i}@example.com", "password": "x"}).status_code
        for i in range(limit + 1)
    ]
    assert statuses[:limit] == [401] * limit
    assert statuses[-1] == 429


def test_demo_login_disabled_by_default(client: TestClient):
    assert client.get("/api/v1/auth/demo-login/status").json() == {"enabled": False}
    assert client.post("/api/v1/auth/demo-login").status_code == 404


def test_demo_login_works_only_in_development(client: TestClient, monkeypatch):
    from app.config import settings as settings_module

    dev = Settings(app_env="development", enable_demo_login=True, secret_key="k" * 40)
    test_env = Settings(app_env="test", enable_demo_login=True, secret_key="k" * 40)
    assert dev.demo_login_enabled is True
    assert test_env.demo_login_enabled is False

    client.app.dependency_overrides[settings_module.get_settings] = lambda: dev
    r = client.post("/api/v1/auth/demo-login")
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "FOUNDER"
    assert client.get("/api/v1/auth/me").status_code == 200


def test_production_refuses_unsafe_configuration():
    with pytest.raises(ValidationError, match="ENABLE_DEMO_LOGIN"):
        Settings(app_env="production", enable_demo_login=True, secret_key="k" * 40)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key="short")
    prod = Settings(app_env="production", secret_key="k" * 40)
    assert prod.secure_cookies is True
    assert prod.demo_login_enabled is False


def test_timestamps_are_serialized_with_utc_offset(founder_client: TestClient):
    # SQLite drops tzinfo; the API must still say these are UTC so browsers show local time.
    me = founder_client.get("/api/v1/auth/me").json()
    assert me["last_login_at"].endswith("Z") or me["last_login_at"].endswith("+00:00")
    assert me["created_at"].endswith("Z") or me["created_at"].endswith("+00:00")


def test_rate_limiter_is_a_sliding_window(monkeypatch):
    from app.security import rate_limit

    clock = [1000.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    limiter = rate_limit.RateLimiter(limit=3, window_seconds=60)
    assert [limiter.hit("ip") for _ in range(4)] == [True, True, True, False]
    clock[0] += 59  # still inside the window: a clock-minute boundary must not reset it
    assert limiter.hit("ip") is False
    clock[0] += 2  # the first hits have aged out
    assert limiter.hit("ip") is True
    assert limiter.hit("other") is True
