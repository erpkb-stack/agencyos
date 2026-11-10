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
from app.security import mfa
from app.security.passwords import hash_password, validate_password_strength, verify_password
from app.security.tokens import hash_session_token, new_session_token
from app.services import audit_service
from app.utils.errors import (
    AppError, AuthenticationError, ConflictError, NotFoundError, PermissionDeniedError, RateLimitedError,
)
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
    from app.security.deps import account_limiter

    key = normalize_email(email)
    if account_limiter.exceeded(key):
        audit_service.record(db, "auth.login_throttled", actor_label=key[:200],
                             summary="Login refused: too many failed attempts for this account", ip_address=ip)
        db.commit()
        raise RateLimitedError("Too many failed attempts for this account; try again later")
    user = db.scalar(select(User).where(User.email == key))
    valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid or not user.is_active:
        account_limiter.hit(key)
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

    if user.totp_enabled:
        # Password is right; a second factor is still needed. No session yet.
        audit_service.record(db, "auth.mfa_challenge", user=user, entity_type="user", entity_id=user.id,
                             summary=f"{user.full_name} entered the correct password; waiting for the 2FA code",
                             ip_address=ip)
        db.commit()
        return MfaChallenge(user=user, token=mfa.issue_mfa_token(user.id, user.password_hash, settings.secret_key))
    account_limiter.clear(key)
    issued = _issue_session(db, user, settings, ip, user_agent)
    audit_service.record(
        db, "auth.login", user=user, entity_type="user", entity_id=user.id,
        summary=f"{user.full_name} signed in", ip_address=ip,
    )
    db.commit()
    return issued


@dataclass
class MfaChallenge:
    user: User
    token: str


def complete_mfa_login(db: Session, settings: Settings, token: str, code: str | None, recovery_code: str | None,
                       ip: str | None = None, user_agent: str | None = None) -> IssuedSession:
    """Second step of sign-in: a valid MFA token plus an authenticator or recovery code."""
    from app.security.deps import account_limiter

    claims = mfa.read_mfa_token(token, settings.secret_key)
    if claims is None:
        raise AuthenticationError("Your sign-in expired; enter your password again")
    user = db.get(User, claims[0])
    if (user is None or not user.is_active or not user.totp_enabled
            or claims[1] != mfa.password_fingerprint(user.password_hash)):
        raise AuthenticationError("Your sign-in expired; enter your password again")
    key = user.email
    if account_limiter.exceeded(key):
        raise RateLimitedError("Too many failed attempts for this account; try again later")
    method = None
    if code:
        secret = mfa.decrypt_secret(user.totp_secret or "", settings.secret_key)
        step = mfa.verify(secret, code, last_step=user.totp_last_step) if secret else None
        if step is not None:
            user.totp_last_step = step
            method = "authenticator code"
    elif recovery_code:
        remaining = mfa.use_recovery_code(user.recovery_codes, recovery_code)
        if remaining is not None:
            user.recovery_codes = remaining
            method = f"recovery code ({len(remaining)} left)"
    if method is None:
        account_limiter.hit(key)
        audit_service.record(db, "auth.mfa_failed", user=user, entity_type="user", entity_id=user.id,
                             summary="Wrong two-factor code", ip_address=ip)
        db.commit()
        raise AuthenticationError("That code is not valid")
    account_limiter.clear(key)
    issued = _issue_session(db, user, settings, ip, user_agent)
    audit_service.record(db, "auth.login", user=user, entity_type="user", entity_id=user.id,
                         summary=f"{user.full_name} signed in with a {method}", ip_address=ip)
    db.commit()
    return issued


# -- two-factor setup --------------------------------------------------------------------------


def start_mfa_setup(db: Session, user: User, settings: Settings) -> dict:
    if user.totp_enabled:
        raise AppError("Two-factor authentication is already on; turn it off first to set up a new device")
    secret = mfa.new_secret()
    user.totp_secret = mfa.encrypt_secret(secret, settings.secret_key)
    user.totp_last_step = None
    db.commit()
    uri = mfa.provisioning_uri(secret, user.email)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": mfa.qr_svg_data_uri(uri)}


def enable_mfa(db: Session, user: User, settings: Settings, code: str) -> list[str]:
    if user.totp_enabled:
        raise AppError("Two-factor authentication is already on")
    secret = mfa.decrypt_secret(user.totp_secret or "", settings.secret_key)
    if not secret:
        raise AppError("Start the setup first")
    step = mfa.verify(secret, code, last_step=None)
    if step is None:
        raise AppError("That code doesn't match. Check the time on your phone and try the newest code.")
    codes = mfa.new_recovery_codes()
    user.totp_enabled, user.totp_enabled_at, user.totp_last_step = True, utcnow(), step
    user.recovery_codes = [mfa.hash_recovery_code(c) for c in codes]
    audit_service.record(db, "auth.mfa_enabled", user=user, entity_type="user", entity_id=user.id,
                         summary=f"{user.full_name} turned on two-factor authentication")
    db.commit()
    return codes


def _check_second_factor(user: User, settings: Settings, code: str | None, recovery_code: str | None) -> bool:
    if code:
        secret = mfa.decrypt_secret(user.totp_secret or "", settings.secret_key)
        step = mfa.verify(secret, code, last_step=user.totp_last_step) if secret else None
        if step is not None:
            user.totp_last_step = step
            return True
    if recovery_code:
        remaining = mfa.use_recovery_code(user.recovery_codes, recovery_code)
        if remaining is not None:
            user.recovery_codes = remaining
            return True
    return False


def _clear_mfa(user: User) -> None:
    user.totp_secret = None
    user.totp_enabled = False
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.recovery_codes = None


def disable_mfa(db: Session, user: User, settings: Settings, password: str, code: str | None,
                recovery_code: str | None) -> None:
    if not user.totp_enabled:
        raise AppError("Two-factor authentication is not on")
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("Password is incorrect")
    if not _check_second_factor(user, settings, code, recovery_code):
        raise AuthenticationError("That code is not valid")
    _clear_mfa(user)
    audit_service.record(db, "auth.mfa_disabled", user=user, entity_type="user", entity_id=user.id,
                         summary=f"{user.full_name} turned off two-factor authentication")
    db.commit()


def regenerate_recovery_codes(db: Session, user: User, settings: Settings, code: str) -> list[str]:
    if not user.totp_enabled:
        raise AppError("Two-factor authentication is not on")
    if not _check_second_factor(user, settings, code, None):
        raise AuthenticationError("That code is not valid")
    codes = mfa.new_recovery_codes()
    user.recovery_codes = [mfa.hash_recovery_code(c) for c in codes]
    audit_service.record(db, "auth.mfa_recovery_regenerated", user=user, entity_type="user", entity_id=user.id,
                         summary="New recovery codes generated; old ones no longer work")
    db.commit()
    return codes


def reset_mfa(db: Session, email: str) -> User:
    """CLI-only: remove 2FA for a locked-out user and sign them out everywhere."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise NotFoundError("No user with that email")
    _clear_mfa(user)
    _revoke_all_sessions(db, user)
    audit_service.record(db, "auth.mfa_reset", actor_label="CLI", entity_type="user", entity_id=user.id,
                         summary=f"Two-factor authentication reset for {user.email} from the command line")
    db.commit()
    return user


# -- sessions ----------------------------------------------------------------------------------


def list_sessions(db: Session, user: User, current_token: str | None) -> list[dict]:
    current = hash_session_token(current_token) if current_token else None
    now = utcnow()
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
                                                UserSession.expires_at > now).order_by(UserSession.last_seen_at.desc()))
    return [{"id": s.id, "created_at": s.created_at, "last_seen_at": s.last_seen_at, "expires_at": s.expires_at,
             "ip_address": s.ip_address, "user_agent": s.user_agent, "current": s.token_hash == current} for s in rows]


def revoke_session(db: Session, user: User, session_id: int, current_token: str | None) -> None:
    s = db.get(UserSession, session_id)
    if s is None or s.user_id != user.id or s.revoked_at is not None:
        raise NotFoundError("Session not found")
    if current_token and s.token_hash == hash_session_token(current_token):
        raise AppError("Use Sign out to end the session you are using now")
    s.revoked_at = utcnow()
    audit_service.record(db, "auth.session_revoked", user=user, entity_type="user", entity_id=user.id,
                         summary=f"{user.full_name} signed out another device")
    db.commit()


def revoke_other_sessions(db: Session, user: User, current_token: str | None) -> int:
    stmt = update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    if current_token:
        stmt = stmt.where(UserSession.token_hash != hash_session_token(current_token))
    count = db.execute(stmt.values(revoked_at=utcnow())).rowcount
    audit_service.record(db, "auth.sessions_revoked", user=user, entity_type="user", entity_id=user.id,
                         summary=f"{user.full_name} signed out {count} other device(s)")
    db.commit()
    return count


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


def _revoke_all_sessions(db: Session, user: User) -> None:
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def update_user(db: Session, actor: User, user_id: int, *, is_active: bool | None, role: UserRole | None) -> User:
    """Activate/deactivate or change a role. Deactivation signs the user out everywhere."""
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.id == actor.id:
        raise PermissionDeniedError("You cannot change your own status or role")
    privileged = {UserRole.FOUNDER, UserRole.ADMIN}
    if actor.role != UserRole.FOUNDER and (user.role in privileged or role in privileged):
        raise PermissionDeniedError("Only a founder can manage founder and admin accounts")
    changes = []
    if is_active is not None and is_active != user.is_active:
        if not is_active and user.role == UserRole.FOUNDER and _active_founders(db) <= 1:
            raise PermissionDeniedError("The last active founder cannot be deactivated")
        user.is_active = is_active
        if not is_active:
            _revoke_all_sessions(db, user)
        changes.append("activated" if is_active else "deactivated")
    if role is not None and role != user.role:
        if user.role == UserRole.FOUNDER and _active_founders(db) <= 1:
            raise PermissionDeniedError("The last founder's role cannot be changed")
        changes.append(f"role {user.role.value} → {role.value}")
        user.role = role
        _revoke_all_sessions(db, user)  # new permissions apply from the next sign-in
    if changes:
        audit_service.record(db, "user.updated", user=actor, entity_type="user", entity_id=user.id,
                             summary=f"{user.email}: {', '.join(changes)}")
        db.commit()
    return user


def _active_founders(db: Session) -> int:
    return len(list(db.scalars(select(User.id).where(User.role == UserRole.FOUNDER, User.is_active.is_(True)))))


def reset_password(db: Session, email: str, new_password: str) -> User:
    """CLI-only reset for someone locked out. Revokes every session."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise NotFoundError("No user with that email")
    _check_strength(new_password)
    user.password_hash = hash_password(new_password)
    _revoke_all_sessions(db, user)
    audit_service.record(db, "auth.password_reset", actor_label="CLI", entity_type="user", entity_id=user.id,
                         summary=f"Password reset for {user.email} from the command line")
    db.commit()
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))
