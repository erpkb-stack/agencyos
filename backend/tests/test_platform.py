"""Health checks, error handling, security headers, frontend serving, settings endpoint."""

from fastapi.testclient import TestClient

from app.security.passwords import hash_password, validate_password_strength, verify_password
from app.services import dashboard_service


def test_health_endpoints(client: TestClient):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["environment"] == "test"
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ok"}


def test_unknown_api_route_returns_json_404(client: TestClient):
    r = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "abc123"})
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert body["request_id"] == "abc123"
    assert r.headers["X-Request-ID"] == "abc123"


def test_unhandled_errors_do_not_leak_internals(founder_client: TestClient, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail: db password=hunter2")

    monkeypatch.setattr(dashboard_service, "summary", boom)
    r = founder_client.get("/api/v1/dashboard/summary")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "hunter2" not in r.text


def test_security_headers(client: TestClient):
    r = client.get("/api/v1/health")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_frontend_pages_served(client: TestClient):
    index = client.get("/")
    assert index.status_code == 200 and "AgencyOS" in index.text
    assert client.get("/login").status_code == 200
    assert client.get("/assets/js/app.js").status_code == 200
    assert client.get("/assets/vendor/chart.umd.min.js").status_code == 200


def test_openapi_docs_available(client: TestClient):
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/api/v1/auth/login" in spec["paths"]
    assert "/api/v1/dashboard/summary" in spec["paths"]


def test_system_settings_never_expose_keys(founder_client: TestClient, monkeypatch):
    from app.config import settings as settings_module
    from app.config.settings import Settings

    cfg = Settings(app_env="test", secret_key="k" * 40, openai_api_key="sk-live-SECRET123")
    founder_client.app.dependency_overrides[settings_module.get_settings] = lambda: cfg
    r = founder_client.get("/api/v1/settings/system")
    assert r.status_code == 200
    assert r.json()["llm"]["openai_configured"] is True
    assert r.json()["external_sending_enabled"] is False
    assert "SECRET123" not in r.text


def test_password_hashing_primitives():
    stored = hash_password("Some-Pass-123")
    assert verify_password("Some-Pass-123", stored)
    assert not verify_password("some-pass-123", stored)
    assert not verify_password("x", "garbage")
    assert hash_password("Some-Pass-123") != stored  # salted
    assert validate_password_strength("Some-Pass-123") == []
    assert len(validate_password_strength("short")) == 3
