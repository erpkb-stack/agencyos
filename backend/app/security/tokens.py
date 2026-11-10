"""Session token and CSRF token primitives."""

from __future__ import annotations

import hashlib
import hmac
import secrets

SESSION_COOKIE = "agencyos_session"
CSRF_COOKIE = "agencyos_csrf"
CSRF_HEADER = "X-CSRF-Token"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_for(session_token: str, secret_key: str) -> str:
    """CSRF token bound to the session: HMAC(secret, session token)."""
    return hmac.new(
        secret_key.encode("utf-8"), session_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def csrf_valid(session_token: str, presented: str | None, secret_key: str) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token_for(session_token, secret_key), presented)
