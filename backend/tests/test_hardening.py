"""Phase 6: security hardening, user administration, CLI tools and runner behaviour."""

from __future__ import annotations

import io
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import PlainTextResponse

from app import cli
from app.config.settings import Settings
from app.middleware import RequestContextMiddleware
from app.models.ai import AgentTask
from app.models.enums import TaskStatus, UserRole
from app.models.identity import AuditLog, User
from app.utils.errors import register_exception_handlers
from tests.conftest import PASSWORD, login

API = "/api/v1"


# -- login throttling --------------------------------------------------------------------------


def test_account_is_throttled_after_repeated_failures(client: TestClient, make_user, db):
    make_user()
    bad = {"email": "founder@example.com", "password": "wrong-password-1A"}
    statuses = [client.post(f"{API}/auth/login", json=bad).status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]
    # Even the right password is refused while the account is throttled.
    r = client.post(f"{API}/auth/login", json={"email": "founder@example.com", "password": PASSWORD})
    assert r.status_code == 429 and "this account" in r.json()["error"]["message"]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.login_throttled"))


def test_successful_login_clears_failures(client: TestClient, make_user):
    make_user()
    bad = {"email": "founder@example.com", "password": "wrong-password-1A"}
    for _ in range(4):
        client.post(f"{API}/auth/login", json=bad)
    login(client, "founder@example.com")
    client.cookies.clear()
    for _ in range(4):
        assert client.post(f"{API}/auth/login", json=bad).status_code == 401


# -- transport and error handling --------------------------------------------------------------


def test_oversized_request_is_rejected_before_parsing(founder_client: TestClient):
    big = b"x" * (5 * 1024 * 1024 + 1)
    r = founder_client.post(f"{API}/prospects/import", files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def _mini_app(**kwargs) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, **kwargs)
    register_exception_handlers(app)

    @app.get("/ok")
    def ok() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.post("/echo")
    def echo() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/conflict")
    def conflict() -> None:
        raise IntegrityError("INSERT …", {}, Exception("UNIQUE constraint failed: users.email"))

    return TestClient(app, raise_server_exceptions=False)


def test_hsts_only_when_secure():
    assert "strict-transport-security" not in _mini_app().get("/ok").headers
    assert _mini_app(hsts=True).get("/ok").headers["strict-transport-security"].startswith("max-age=31536000")


def test_chunked_body_without_length_is_refused():
    def gen():
        yield b"abc"

    r = _mini_app().post("/echo", content=gen())
    assert r.status_code == 411


def test_request_id_is_sanitised():
    c = _mini_app()
    assert c.get("/ok", headers={"X-Request-ID": "abc-123_x.y"}).headers["x-request-id"] == "abc-123_x.y"
    replaced = c.get("/ok", headers={"X-Request-ID": "bad id <script>"}).headers["x-request-id"]
    assert replaced != "bad id <script>" and len(replaced) == 32


def test_integrity_errors_become_conflicts_without_details():
    r = _mini_app().get("/conflict")
    assert r.status_code == 409
    assert "UNIQUE" not in r.text and r.json()["error"]["code"] == "conflict"


def test_production_defaults_and_warnings():
    dev = Settings(app_env="development", secret_key="k" * 40)
    prod = Settings(app_env="production", secret_key="k" * 40, llm_provider="anthropic")
    assert dev.api_docs_enabled is True and prod.api_docs_enabled is False
    assert Settings(app_env="production", secret_key="k" * 40, enable_api_docs=True).api_docs_enabled is True
    warnings = " ".join(prod.production_warnings())
    assert "PostgreSQL" in warnings and "https://" in warnings and "ANTHROPIC_API_KEY" in warnings
    ok = Settings(app_env="production", secret_key="k" * 40, database_url="postgresql+psycopg://u:p@db/x",
                  public_base_url="https://os.example.com", agency_name="A", agency_address="B", sender_name="C")
    assert ok.production_warnings() == []


def test_system_settings_reports_warnings(founder_client: TestClient):
    body = founder_client.get(f"{API}/settings/system").json()
    # APP_ENV=test is not production, so the API docs default to on; agency identity is unset here.
    assert body["api_docs_enabled"] is True
    assert any("Agency identity" in w for w in body["warnings"])


def test_exports_are_audited(founder_client: TestClient, db):
    founder_client.post(f"{API}/prospects", json={"company_name": "Acme"})
    founder_client.get(f"{API}/prospects/export")
    founder_client.get(f"{API}/analytics/reports/sales_conversion/csv")
    actions = set(db.scalars(select(AuditLog.action)))
    assert {"prospect.exported", "report.exported"} <= actions


# -- user administration -----------------------------------------------------------------------


def test_founder_can_deactivate_and_reactivate_staff(client: TestClient, make_user):
    make_user()
    staff_user = make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    founder = login(TestClient(client.app), "founder@example.com")
    staff = login(TestClient(client.app), "staff@example.com")
    r = founder.patch(f"{API}/auth/users/{staff_user.id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert staff.get(f"{API}/auth/me").status_code == 401  # signed out everywhere
    assert founder.patch(f"{API}/auth/users/{staff_user.id}", json={"is_active": True, "role": "READ_ONLY"}).json()["role"] == "READ_ONLY"


def test_user_admin_rules(client: TestClient, make_user):
    founder_user = make_user()
    admin_user = make_user(email="admin@example.com", role=UserRole.ADMIN, name="Ada Admin")
    staff_user = make_user(email="staff@example.com", role=UserRole.STAFF, name="Sam Staff")
    founder = login(TestClient(client.app), "founder@example.com")
    admin = login(TestClient(client.app), "admin@example.com")
    assert admin.patch(f"{API}/auth/users/{founder_user.id}", json={"is_active": False}).status_code == 403
    assert admin.patch(f"{API}/auth/users/{staff_user.id}", json={"role": "ADMIN"}).status_code == 403
    assert admin.patch(f"{API}/auth/users/{staff_user.id}", json={"is_active": False}).status_code == 200
    assert founder.patch(f"{API}/auth/users/{founder_user.id}", json={"is_active": False}).status_code == 403  # self
    assert founder.patch(f"{API}/auth/users/{admin_user.id}", json={"role": "STAFF"}).status_code == 200
    assert founder.patch(f"{API}/auth/users/999", json={"is_active": False}).status_code == 404
    assert founder.patch(f"{API}/auth/users/{admin_user.id}", json={"unknown": 1}).status_code == 422


def test_last_founder_is_protected(db, make_user):
    from app.services import auth_service
    from app.utils.errors import PermissionDeniedError

    only_founder = make_user()
    second = make_user(email="f2@example.com", role=UserRole.FOUNDER, name="Second Founder")
    auth_service.update_user(db, second, only_founder.id, is_active=False, role=None)  # two founders: allowed
    with pytest.raises(PermissionDeniedError):
        auth_service.update_user(db, only_founder, second.id, is_active=False, role=None)


# -- CLI ---------------------------------------------------------------------------------------


@pytest.fixture
def cli_db(session_factory, monkeypatch):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    return session_factory


def test_cli_user_lifecycle(cli_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Strong-Pass-123\n"))
    assert cli.main(["create-user", "--email", "Boss@Example.com", "--name", "Boss", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("weak\n"))
    assert cli.main(["reset-password", "--email", "boss@example.com", "--password-stdin"]) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO("Another-Pass-456\n"))
    assert cli.main(["reset-password", "--email", "boss@example.com", "--password-stdin"]) == 0
    assert cli.main(["set-active", "--email", "boss@example.com", "--inactive"]) == 0
    assert cli.main(["set-active", "--email", "nobody@example.com", "--active"]) == 1
    out = capsys.readouterr().out
    assert "Created FOUNDER user boss@example.com" in out and "now inactive" in out
    with cli_db() as db:
        user = db.scalar(select(User).where(User.email == "boss@example.com"))
        assert user.is_active is False
        actions = set(db.scalars(select(AuditLog.action)))
    assert {"user.created", "auth.password_reset", "user.updated"} <= actions


def test_cli_backup_and_check_config(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(app_env="test", secret_key="k" * 40,
                                                               database_url=f"sqlite:///{source}"))
    dest = tmp_path / "backups"
    for _ in range(3):
        assert cli.main(["backup-db", "--dest", str(dest), "--keep", "2"]) == 0
        time.sleep(1.05)  # distinct timestamped filenames
    backups = sorted(dest.glob("agencyos-*.db"))
    assert len(backups) == 2
    with sqlite3.connect(backups[-1]) as conn:
        assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    assert oct(backups[-1].stat().st_mode)[-3:] == "600"
    assert cli.main(["check-config"]) == 0
    assert "Environment: test" in capsys.readouterr().out

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(app_env="test", secret_key="k" * 40,
                                                               database_url="postgresql+psycopg://u:p@h/db"))
    assert cli.main(["backup-db"]) == 1


# -- runner and health -------------------------------------------------------------------------


def test_readiness_reports_database_failure(client: TestClient):
    from app.database.session import get_db

    class Broken:
        def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    client.app.dependency_overrides[get_db] = lambda: Broken()
    r = client.get(f"{API}/health/ready")
    assert r.status_code == 503 and r.json()["database"] == "error"
