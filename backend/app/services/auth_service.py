"""Authentication, sessions and user management. No FastAPI imports here."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.enums import UserRole
from app.models.identity import User, UserSession
from app.security.passwords import hash_password, validate_password_strength, verify_password
from app.security.tokens import hash_session_token, new_session_token
from app.services import audit_service
from app.utils.errors import AppError, AuthenticationError, ConflictError, PermissionDeniedError
from app.utils.time import ensure_aware, utcnow

DEMO_USER_EMAIL = "demo.founder@agencyos.local"
_SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
# Verified when the email is unknown, so response time does not reveal which emails exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class IssuedSession:
    user: User
    token: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _issue_session(
    db: Session, user: User, settings: Settings, ip: str | None, user_agent: str | None
) -> IssuedSession:
    token = new_session_token()
    now = utcnow()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
            ip_address=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    user.last_login_at = now
    return IssuedSession(user=user, token=token)


def login(
    db: Session,
    settings: Settings,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid or not user.is_active:
        audit_service.record(
            db,
            "auth.login_failed",
            user=user,
            actor_label=normalize_email(email)[:200],
            summary="Failed login attempt",
            ip_address=ip,
        )
        db.commit()
        raise AuthenticationError("Invalid email or password")

    issued = _issue_session(db, user, settings, ip, user_agent)
    audit_service.record(
        db, "auth.login", user=user, entity_type="user", entity_id=user.id,
        summary=f"{user.full_name} signed in", ip_address=ip,
    )
    db.commit()
    return issued


def demo_login(
    db: Session, settings: Settings, ip: str | None = None, user_agent: str | None = None
) -> IssuedSession:
    if not settings.demo_login_enabled:
        # Callers translate this to 404 so the endpoint looks absent when disabled.
        raise PermissionDeniedError("Demo login is disabled")
    user = db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    if user is None:
        # Random password nobody knows: the demo account is reachable only through this gate.
        user = User(
            email=DEMO_USER_EMAIL,
            full_name="Demo Founder",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=UserRole.FOUNDER,
        )
        db.add(user)
        db.flush()
    issued = _issue_session(db, user, settings, ip, user_agent)
    audit_service.record(
        db, "auth.demo_login", user=user, entity_type="user", entity_id=user.id,
        summary="Development demo login", ip_address=ip,
    )
    db.commit()
    return issued


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    if session is None or session.revoked_at is not None:
        return None
    now = utcnow()
    if ensure_aware(session.expires_at) <= now:
        return None
    user = session.user
    if not user.is_active:
        return None
    if now - ensure_aware(session.last_seen_at) > _SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return user


def logout(db: Session, token: str | None, user: User | None, ip: str | None = None) -> None:
    if not token:
        return
    db.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_session_token(token))
        .values(revoked_at=utcnow())
    )
    if user:
        audit_service.record(
            db, "auth.logout", user=user, entity_type="user", entity_id=user.id,
            summary=f"{user.full_name} signed out", ip_address=ip,
        )
    db.commit()


def _check_strength(password: str) -> None:
    problems = validate_password_strength(password)
    if problems:
        raise AppError("Password is too weak", details=[f"Password {p}" for p in problems])


def change_password(
    db: Session, user: User, current_password: str, new_password: str, keep_token: str | None
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect")
    _check_strength(new_password)
    user.password_hash = hash_password(new_password)
    # Revoke every other session for this user.
    stmt = update(UserSession).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
    )
    if keep_token:
        stmt = stmt.where(UserSession.token_hash != hash_session_token(keep_token))
    db.execute(stmt.values(revoked_at=utcnow()))
    audit_service.record(
        db, "auth.password_changed", user=user, entity_type="user", entity_id=user.id,
        summary=f"{user.full_name} changed their password",
    )
    db.commit()


def create_user(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
    role: UserRole,
    actor: User | None = None,
) -> User:
    """Create a user. ``actor=None`` means the trusted CLI bootstrap."""
    if actor is not None:
        if actor.role not in (UserRole.FOUNDER, UserRole.ADMIN):
            raise PermissionDeniedError("Only founders and admins can create users")
        if role in (UserRole.FOUNDER, UserRole.ADMIN) and actor.role != UserRole.FOUNDER:
            raise PermissionDeniedError("Only a founder can create founder or admin accounts")
    email = normalize_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ConflictError("A user with this email already exists")
    _check_strength(password)
    user = User(
        email=email, full_name=full_name.strip(), password_hash=hash_password(password), role=role
    )
    db.add(user)
    db.flush()
    audit_service.record(
        db, "user.created", user=actor, entity_type="user", entity_id=user.id,
        actor_label=actor.full_name if actor else "CLI",
        summary=f"User {user.email} created with role {role.value}",
    )
    db.commit()
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))
