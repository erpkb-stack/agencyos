"""Small, dependency-free validators and normalisers for business contact fields."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_CHARS = re.compile(r"^[0-9+().\-\s x/ext]+$", re.IGNORECASE)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def normalize_email(value: str | None) -> str | None:
    value = clean_text(value)
    if value is None:
        return None
    value = value.lower()
    if not _EMAIL.match(value):
        raise ValueError("is not a valid email address")
    return value


def normalize_phone(value: str | None) -> str | None:
    value = clean_text(value)
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    if not _PHONE_CHARS.match(value) or not 7 <= len(digits) <= 15:
        raise ValueError("is not a valid phone number")
    return value


def phone_digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    # Compare US numbers with or without the leading country code.
    return digits[-10:] if len(digits) >= 10 else digits or None


def normalize_website(value: str | None) -> str | None:
    value = clean_text(value)
    if value is None:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise ValueError("is not a valid website address")
    return value


def domain_of(website: str | None) -> str | None:
    """``https://www.Example.com:443/x`` -> ``example.com`` (used for duplicate detection)."""
    if not website:
        return None
    try:
        host = urlsplit(website if "://" in website else "https://" + website).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def company_key(name: str | None) -> str:
    """Loose company-name key: case, punctuation and common suffixes ignored."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    suffixes = {"inc", "llc", "ltd", "co", "corp", "company", "the", "and"}
    return " ".join(w for w in words if w not in suffixes)
