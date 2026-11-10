"""Shared fixtures. Each test gets a fresh SQLite database file; no live services are used."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

# Configure the environment before the app (and its cached settings) is imported.
_TMP = Path(tempfile.mkdtemp(prefix="agencyos-tests-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "SECRET_KEY": "test-secret-key-0123456789-0123456789-abcdef",
        "DATABASE_URL": f"sqlite:///{_TMP / 'bootstrap.db'}",
        "ENABLE_DEMO_LOGIN": "false",
        "LLM_PROVIDER": "mock",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401
from app.config.settings import get_settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import build_engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.security.deps import account_limiter, api_limiter, login_limiter  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from app.security.tokens import CSRF_COOKIE, CSRF_HEADER  # noqa: E402
from app.orchestrator import get_task_runner  # noqa: E402
from app.orchestrator.engine import Orchestrator  # noqa: E402
from app.orchestrator.runner import InlineRunner  # noqa: E402
from app.providers.mock import MockLLMProvider  # noqa: E402

PASSWORD = "Correct-Horse-42"


# Set TEST_DATABASE_URL (e.g. postgresql+psycopg://…) to run the whole suite against PostgreSQL.
# The database is wiped before each test, so never point it at real data.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture
def engine(tmp_path: Path):
    if TEST_DATABASE_URL:
        eng = build_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(eng)
    else:
        eng = build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(eng)
    yield eng
    if TEST_DATABASE_URL:
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    with session_factory() as session:
        yield session


class ProviderSwitch:
    """Tests set ``.provider`` to a fake; by default every agent uses the offline mock."""

    def __init__(self) -> None:
        self.provider = MockLLMProvider()
        self.requested: list[str] = []

    def __call__(self, name: str):
        self.requested.append(name)
        return self.provider


@pytest.fixture
def llm() -> ProviderSwitch:
    return ProviderSwitch()


@pytest.fixture
def orchestrator(session_factory, llm) -> Orchestrator:
    return Orchestrator(session_factory, llm, get_settings())


@pytest.fixture
def client(session_factory, orchestrator) -> Iterator[TestClient]:
    def _get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    # Tasks run synchronously inside the request so tests are deterministic.
    app.dependency_overrides[get_task_runner] = lambda: InlineRunner(orchestrator)
    login_limiter.reset()
    api_limiter.reset()
    account_limiter.reset()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def make_user(db: Session):
    def _make(
        email: str = "founder@example.com",
        role: UserRole = UserRole.FOUNDER,
        password: str = PASSWORD,
        active: bool = True,
        name: str = "Fran Founder",
    ) -> User:
        user = User(
            email=email,
            full_name=name,
            password_hash=hash_password(password),
            role=role,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user

    return _make


def login(client: TestClient, email: str, password: str = PASSWORD) -> TestClient:
    """Sign in and attach the CSRF header for subsequent writes."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
    return client


@pytest.fixture
def founder_client(client: TestClient, make_user) -> TestClient:
    make_user()
    return login(client, "founder@example.com")
