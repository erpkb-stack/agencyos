"""AgencyOS command line.

    python -m app.cli init-db
    python -m app.cli create-user --email you@example.com --name "Your Name" --role FOUNDER
"""

from __future__ import annotations

import argparse
import getpass
import sys

from alembic import command
from alembic.config import Config

from app.config.settings import BACKEND_DIR, DATA_DIR, STORAGE_DIR
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
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agencyos")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Apply all database migrations")
    cu = sub.add_parser("create-user", help="Create a login user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", default="FOUNDER", choices=[r.value for r in UserRole])
    cu.add_argument("--password-stdin", action="store_true", help="Read password from stdin")
    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db()
        return 0
    return create_user(args.email, args.name, args.role, args.password_stdin)


if __name__ == "__main__":
    raise SystemExit(main())
