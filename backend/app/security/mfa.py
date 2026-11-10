"""Two-factor authentication: TOTP (RFC 6238), recovery codes, encrypted secrets, MFA tokens.

* TOTP: 6 digits, 30-second steps, SHA-1 (what every authenticator app supports). A window of
  ±1 step tolerates clock drift; a used step is remembered so a code can't be replayed.
* Secrets are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) under a key derived from
  ``SECRET_KEY`` with HKDF. Changing ``SECRET_KEY`` therefore invalidates enrolled authenticators;
  use ``python -m app.cli reset-2fa`` to let a user enrol again.
* Recovery codes are random, shown once, stored as SHA-256 hashes and single-use.
* Between the password step and the code step the client holds a short-lived, signed MFA token
  (not a session): it proves the password was correct and expires after 5 minutes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
MFA_TOKEN_TTL = 300
RECOVERY_CODES = 10


# -- TOTP --------------------------------------------------------------------------------------


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded)


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret_b32: str, step: int) -> str:
    return hotp(_key(secret_b32), step)


def verify(secret_b32: str, code: str, *, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched step if ``code`` is valid and newer than ``last_step``; otherwise None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if last_step is not None and candidate <= last_step:
            continue  # already used (replay) or older than a used code
        if hmac.compare_digest(code_at(secret_b32, candidate), code):
            return candidate
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str = "AgencyOS") -> str:
    query = urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS,
                       "period": STEP_SECONDS})
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?{query}"


def qr_svg_data_uri(text: str) -> str:
    import segno

    import io

    buf = io.BytesIO()  # a standalone SVG (with xmlns): an inline fragment won't render as an <img>
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111111", light="#ffffff",
                                     xmldecl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


# -- secret encryption -------------------------------------------------------------------------


def _fernet(secret_key: str) -> Fernet:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"agencyos-totp-v1", info=b"totp-secret").derive(
        secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(secret_b32: str, secret_key: str) -> str:
    return _fernet(secret_key).encrypt(secret_b32.encode()).decode()


def decrypt_secret(token: str, secret_key: str) -> str | None:
    try:
        return _fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken:
        return None


# -- recovery codes ----------------------------------------------------------------------------


def new_recovery_codes(n: int = RECOVERY_CODES) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2)) for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()


def use_recovery_code(stored: list[str] | None, code: str) -> list[str] | None:
    """Return the remaining hashes if ``code`` matched (removing it); None if it didn't."""
    if not stored:
        return None
    digest = hash_recovery_code(code)
    for h in stored:
        if hmac.compare_digest(h, digest):
            return [x for x in stored if x != h]
    return None


# -- MFA token (between password and code) -----------------------------------------------------


def _sign(payload: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode(), f"mfa:{payload}".encode(), hashlib.sha256).hexdigest()


def issue_mfa_token(user_id: int, password_hash: str, secret_key: str, now: float | None = None) -> str:
    """Bound to the user's current password hash, so a password change invalidates pending logins."""
    expires = int((time.time() if now is None else now) + MFA_TOKEN_TTL)
    pw = hashlib.sha256(password_hash.encode()).hexdigest()[:16]
    payload = f"{user_id}.{expires}.{pw}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload, secret_key)}"


def read_mfa_token(token: str, secret_key: str, now: float | None = None) -> tuple[int, str] | None:
    """Return (user_id, password-hash fingerprint) if the token is genuine and unexpired."""
    try:
        user_id, expires, pw, nonce, signature = token.split(".")
    except (ValueError, AttributeError):
        return None
    payload = f"{user_id}.{expires}.{pw}.{nonce}"
    if not hmac.compare_digest(_sign(payload, secret_key), signature):
        return None
    if int(expires) < (time.time() if now is None else now):
        return None
    return int(user_id), pw


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]
