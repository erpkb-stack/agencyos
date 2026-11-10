"""Alembic migrations build the full schema and stay in sync with the ORM models."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.config.settings import BACKEND_DIR
from app.database.base import Base
from app.database.session import build_engine


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


def test_upgrade_head_matches_models(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_config(url), "head")

    engine = build_engine(url)
    try:
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert tables == set(Base.metadata.tables)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "render_as_batch": True}
            )
            diff = compare_metadata(ctx, Base.metadata)
        assert diff == [], f"Models and migrations differ; run alembic revision --autogenerate: {diff}"
    finally:
        engine.dispose()


def test_downgrade_to_base(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = build_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()
