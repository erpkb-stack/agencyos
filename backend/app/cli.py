"""AgencyOS command line.

    python -m app.cli init-db
    python -m app.cli create-user --email you@example.com --name "Your Name" --role FOUNDER
    python -m app.cli reset-password --email you@example.com
    python -m app.cli set-active --email someone@example.com --inactive
    python -m app.cli reset-2fa --email someone@example.com
    python -m app.cli backup-db [--dest DIR] [--keep 14]
    python -m app.cli check-config
    python -m app.cli worker
"""

from __future__ import annotations

import argparse
import getpass
import sys

from alembic import command
from alembic.config import Config

from app.config.settings import BACKEND_DIR, DATA_DIR, STORAGE_DIR, get_settings
from app.database.session import SessionLocal
from app.models.enums import UserRole
from app.services import auth_service
from app.utils.errors import AppError


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")
    from app.services import agent_service

    with SessionLocal() as db:
        added = agent_service.ensure_seeded(db)
    print(f"Database is up to date. Built-in agents added: {added}.")


def create_user(email: str, name: str, role: str, password_stdin: bool) -> int:
    password = _read_password(password_stdin)
    if password is None:
        return 1
    with SessionLocal() as db:
        try:
            user = auth_service.create_user(
                db, email=email, full_name=name, password=password, role=UserRole(role)
            )
        except AppError as exc:
            print(f"Error: {exc.message}", file=sys.stderr)
            for detail in exc.details or []:
                print(f"  - {detail}", file=sys.stderr)
            return 1
    print(f"Created {user.role.value} user {user.email} (id {user.id}).")
    return 0


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return None
    return password


def reset_password(email: str, password_stdin: bool) -> int:
    password = _read_password(password_stdin)
    if password is None:
        return 1
    with SessionLocal() as db:
        try:
            user = auth_service.reset_password(db, email, password)
        except AppError as exc:
            print(f"Error: {exc.message}", file=sys.stderr)
            for detail in exc.details or []:
                print(f"  - {detail}", file=sys.stderr)
            return 1
    print(f"Password reset for {user.email}. All of their sessions were signed out.")
    return 0


def reset_2fa(email: str) -> int:
    with SessionLocal() as db:
        try:
            user = auth_service.reset_mfa(db, email)
        except AppError as exc:
            print(f"Error: {exc.message}", file=sys.stderr)
            return 1
    print(f"Two-factor authentication removed for {user.email}; they were signed out and can set it up again.")
    return 0


def set_active(email: str, active: bool) -> int:
    from sqlalchemy import select, update

    from app.models.identity import User, UserSession
    from app.services import audit_service
    from app.utils.time import utcnow

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == auth_service.normalize_email(email)))
        if user is None:
            print("Error: no user with that email", file=sys.stderr)
            return 1
        user.is_active = active
        if not active:
            db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                       .values(revoked_at=utcnow()))
        audit_service.record(db, "user.updated", actor_label="CLI", entity_type="user", entity_id=user.id,
                             summary=f"{user.email} {'activated' if active else 'deactivated'} from the command line")
        db.commit()
    print(f"{email} is now {'active' if active else 'inactive'}.")
    return 0


def backup_db(dest: str | None, keep: int) -> int:
    """Consistent online backup of the SQLite database (safe while the app is running)."""
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    settings = get_settings()
    if settings.database_backend != "sqlite":
        print("This command backs up SQLite. For PostgreSQL use pg_dump (see docs/DEPLOYMENT.md).", file=sys.stderr)
        return 1
    source = Path(settings.database_url.split("///", 1)[1])
    if not source.exists():
        print(f"Error: database file not found: {source}", file=sys.stderr)
        return 1
    target_dir = Path(dest) if dest else DATA_DIR / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"agencyos-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    with sqlite3.connect(target) as check:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        print(f"Error: backup failed its integrity check ({result})", file=sys.stderr)
        return 1
    target.chmod(0o600)
    backups = sorted(target_dir.glob("agencyos-*.db"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
    print(f"Backup written to {target} ({target.stat().st_size:,} bytes, integrity ok). Kept {min(len(backups), keep)}.")
    return 0


def worker() -> int:
    """Run the agent task queue in this process until interrupted (Ctrl+C / SIGTERM)."""
    import signal
    import threading

    from app.database.session import SessionLocal as Session_
    from app.orchestrator import build_email_sender, build_worker
    from app.orchestrator.runner import recover_interrupted

    settings = get_settings()
    with Session_() as db:
        recovered = recover_interrupted(db)
    print(f"Worker started: {settings.task_workers} request(s) at a time, "
          f"{settings.orchestrator_parallel_agents} agents per request. "
          f"Re-queued {recovered['requeued']}, failed {recovered['failed']} interrupted task(s).", flush=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    queue = build_worker().start()
    sender = build_email_sender().start()
    stop.wait()
    print("Stopping worker; running tasks will be re-queued on next start.", flush=True)
    sender.shutdown()
    queue.shutdown(wait=False)
    return 0


def check_config() -> int:
    settings = get_settings()
    print(f"Environment: {settings.app_env.value}")
    print(f"Database: {settings.database_backend}")
    print(f"LLM provider: {settings.llm_provider.value}")
    print(f"Secure cookies: {settings.secure_cookies} · API docs: {settings.api_docs_enabled}")
    warnings = settings.production_warnings()
    for w in warnings:
        print(f"WARNING: {w}")
    if not warnings:
        print("No configuration warnings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agencyos")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Apply all database migrations")
    cu = sub.add_parser("create-user", help="Create a login user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", default="FOUNDER", choices=[r.value for r in UserRole])
    cu.add_argument("--password-stdin", action="store_true", help="Read password from stdin")
    rp = sub.add_parser("reset-password", help="Set a new password for a user and sign them out everywhere")
    rp.add_argument("--email", required=True)
    rp.add_argument("--password-stdin", action="store_true", help="Read password from stdin")
    r2 = sub.add_parser("reset-2fa", help="Remove two-factor authentication for a locked-out user")
    r2.add_argument("--email", required=True)
    sa = sub.add_parser("set-active", help="Activate or deactivate a user")
    sa.add_argument("--email", required=True)
    group = sa.add_mutually_exclusive_group(required=True)
    group.add_argument("--active", dest="active", action="store_true")
    group.add_argument("--inactive", dest="active", action="store_false")
    bk = sub.add_parser("backup-db", help="Back up the SQLite database")
    bk.add_argument("--dest", help="Directory for backups (default data/backups)")
    bk.add_argument("--keep", type=int, default=14, help="How many backups to keep (default 14)")
    sub.add_parser("check-config", help="Print configuration and production warnings")
    sub.add_parser("worker", help="Run the agent task queue (use with RUN_WORKER_IN_PROCESS=false)")
    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db()
        return 0
    if args.command == "reset-password":
        return reset_password(args.email, args.password_stdin)
    if args.command == "reset-2fa":
        return reset_2fa(args.email)
    if args.command == "set-active":
        return set_active(args.email, args.active)
    if args.command == "backup-db":
        return backup_db(args.dest, args.keep)
    if args.command == "check-config":
        return check_config()
    if args.command == "worker":
        return worker()
    return create_user(args.email, args.name, args.role, args.password_stdin)


if __name__ == "__main__":
    raise SystemExit(main())
