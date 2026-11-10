"""Email rules enforced before anything can be approved for sending.

Based on CAN-SPAM style requirements: identify the sender, include a valid postal address,
offer a working unsubscribe, avoid deceptive subject lines, and never email suppressed addresses.
This is a safety net, not legal advice.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

MERGE_FIELD = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Fields AgencyOS can fill from a prospect record or the agency settings.
PROSPECT_FIELDS = {"first_name", "last_name", "contact_name", "contact_title", "company_name", "city",
                   "state", "industry", "website", "observation"}
AGENCY_FIELDS = {"sender_name", "sender_email", "agency_name", "agency_address", "unsubscribe_link",
                 "landing_page_url", "calendar_link"}
KNOWN_FIELDS = PROSPECT_FIELDS | AGENCY_FIELDS

SPAM_PHRASES = ("100% free", "act now", "guaranteed", "risk-free", "risk free", "no obligation",
                "free money", "winner", "urgent!!!", "click here now")


def issue(level: str, code: str, message: str) -> dict[str, str]:
    return {"level": level, "code": code, "message": message}


def merge_fields(text: str) -> set[str]:
    return {m.lower() for m in MERGE_FIELD.findall(text or "")}


def check_email(subject: str, body: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    subject = (subject or "").strip()
    body = body or ""
    fields = merge_fields(subject) | merge_fields(body)
    low_body = body.lower()

    if not subject:
        issues.append(issue("error", "subject_missing", "Subject line is empty"))
    elif len(subject) > 150:
        issues.append(issue("error", "subject_too_long", "Subject line is longer than 150 characters"))
    if not body.strip():
        issues.append(issue("error", "body_missing", "Email body is empty"))
    if "unsubscribe_link" not in fields and "unsubscribe" not in low_body:
        issues.append(issue("error", "no_unsubscribe",
                            "Add an unsubscribe option, e.g. {{unsubscribe_link}}"))
    elif "unsubscribe_link" not in fields:
        issues.append(issue("warning", "unsubscribe_text_only",
                            "Unsubscribe is mentioned but there is no {{unsubscribe_link}} merge field"))
    if not ({"sender_name", "agency_name"} & fields):
        issues.append(issue("error", "no_sender", "Identify the sender with {{sender_name}} or {{agency_name}}"))
    if "agency_address" not in fields:
        issues.append(issue("error", "no_postal_address",
                            "Include the agency's postal address with {{agency_address}}"))
    if re.match(r"^\s*(re|fw|fwd)\s*:", subject, re.IGNORECASE):
        issues.append(issue("warning", "reply_prefix",
                            "Subject starts with Re:/Fwd:. Only use this for a genuine reply in the same thread"))
    letters = [c for c in subject if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        issues.append(issue("warning", "shouting", "Subject is mostly capital letters"))
    for phrase in SPAM_PHRASES:
        if phrase in (subject + " " + body).lower():
            issues.append(issue("warning", "spam_phrase", f"Avoid spam-trigger phrase: “{phrase}”"))
    unknown = sorted(fields - KNOWN_FIELDS)
    if unknown:
        issues.append(issue("warning", "unknown_merge_fields",
                            "These merge fields cannot be filled automatically: " + ", ".join(unknown)))
    return issues


def has_errors(issues: list[dict[str, str]]) -> bool:
    return any(i["level"] == "error" for i in issues)


# -- unsubscribe tokens ------------------------------------------------------------------------


def unsubscribe_token(email: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"unsub:{email.strip().lower()}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_valid(email: str, token: str, secret: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(email, secret), token or "")


# -- personalisation ---------------------------------------------------------------------------


def prospect_values(p: Any) -> dict[str, str | None]:
    contact = (p.contact_name or "").split()
    return {
        "first_name": contact[0] if contact else None,
        "last_name": contact[-1] if len(contact) > 1 else None,
        "contact_name": p.contact_name, "contact_title": p.contact_title, "company_name": p.company_name,
        "city": p.city, "state": p.state, "industry": p.industry, "website": p.website,
        "observation": p.observations,
    }


def render(text: str, values: dict[str, str | None]) -> tuple[str, list[str]]:
    """Fill merge fields. Missing values stay visible as [missing: field] so nothing is invented."""
    missing: list[str] = []

    def sub(match: re.Match) -> str:
        key = match.group(1).lower()
        value = values.get(key)
        if value:
            return str(value)
        if key not in missing:
            missing.append(key)
        return f"[missing: {key}]"

    return MERGE_FIELD.sub(sub, text or ""), missing
