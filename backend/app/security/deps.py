"""FastAPI dependencies: current user, role checks, CSRF and API rate limiting."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.models.enums import UserRole
from app.models.identity import User
from app.security.rate_limit import RateLimiter
from app.security.tokens import CSRF_HEADER, SESSION_COOKIE, csrf_valid
from app.services import auth_service
from app.utils.errors import AuthenticationError, PermissionDeniedError, RateLimitedError

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_settings = get_settings()
login_limiter = RateLimiter(_settings.login_rate_limit_per_minute)
# Failed logins per account, so one account can't be guessed at from many IP addresses.
account_limiter = RateLimiter(_settings.login_account_limit, _settings.login_account_window_seconds)
api_limiter = RateLimiter(_settings.api_rate_limit_per_minute)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_api_rate_limit(request: Request) -> None:
    if not api_limiter.hit(client_ip(request)):
        raise RateLimitedError("Too many requests, slow down")


def enforce_login_rate_limit(request: Request) -> None:
    if not login_limiter.hit(client_ip(request)):
        raise RateLimitedError("Too many login attempts, try again in a minute")


def enforce_csrf(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Unsafe requests that carry a session cookie must present the bound CSRF token."""
    if request.method not in _UNSAFE_METHODS:
        return
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return  # no ambient credentials -> nothing to forge
    if not csrf_valid(token, request.headers.get(CSRF_HEADER), settings.secret_key):
        exc = PermissionDeniedError("Missing or invalid CSRF token")
        exc.code = "csrf_failed"
        raise exc


def get_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = auth_service.resolve_session(db, get_session_token(request))
    if user is None:
        raise AuthenticationError("Authentication required")
    request.state.user_id = user.id
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    allowed = set(roles)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError("Your role does not allow this action")
        return user

    return _dep


require_admin = require_roles(UserRole.FOUNDER, UserRole.ADMIN)
require_writer = require_roles(UserRole.FOUNDER, UserRole.ADMIN, UserRole.STAFF)
require_founder = require_roles(UserRole.FOUNDER)
