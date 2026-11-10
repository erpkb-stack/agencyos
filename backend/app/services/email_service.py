"""Email content: templates and sequence drafts, compliance, personalisation preview, audience
segments, suppression list and unsubscribe handling. Sending lives in `email_sending`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.marketing import compliance
from app.models.crm import Prospect
from app.models.enums import ActorType, CampaignStatus, ContentStatus, EmailKind, SuppressionReason
from app.models.identity import User
from app.models.marketing import Campaign, EmailSuppression, EmailTemplate
from app.services import audit_service
from app.utils.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.utils.time import utcnow
from app.utils.validation import normalize_email

PROTECTED_REASONS = {SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINT}


# -- templates ---------------------------------------------------------------------------------


def get(db: Session, template_id: int) -> EmailTemplate:
    t = db.get(EmailTemplate, template_id)
    if t is None or t.deleted_at is not None:
        raise NotFoundError("Email not found")
    return t


def serialize(t: EmailTemplate) -> dict[str, Any]:
    issues = compliance.check_email(t.subject, t.body_text)
    return {
        "id": t.id, "name": t.name, "kind": t.kind.value, "subject": t.subject, "body_text": t.body_text,
        "campaign_id": t.campaign_id, "client_id": t.client_id, "sequence_order": t.sequence_order,
        "delay_days": t.delay_days, "status": t.status.value, "compliance": issues,
        "compliant": not compliance.has_errors(issues), "created_at": t.created_at, "updated_at": t.updated_at,
    }


def _campaign(db: Session, campaign_id: int | None) -> Campaign | None:
    if campaign_id is None:
        return None
    c = db.get(Campaign, campaign_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Campaign not found")
    return c


def _guard_campaign_edit(db: Session, user: User, campaign: Campaign | None) -> None:
    """Content can't change behind an approval: approved campaigns go back to draft on edit."""
    if campaign is None:
        return
    if campaign.status in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED, CampaignStatus.COMPLETED,
                           CampaignStatus.CANCELLED):
        raise AppError(f"The campaign is {campaign.status.value.lower()}; its emails can no longer be edited")
    if campaign.status == CampaignStatus.AWAITING_APPROVAL:
        raise AppError("The campaign is awaiting approval; reject or wait for the decision before editing")
    if campaign.status == CampaignStatus.APPROVED:
        campaign.status = CampaignStatus.DRAFT
        for t in db.scalars(select(EmailTemplate).where(EmailTemplate.campaign_id == campaign.id)):
            t.status = ContentStatus.DRAFT
        from sqlalchemy import update

        from app.models.enums import EmailMessageStatus as MS
        from app.models.marketing import EmailMessage

        db.execute(update(EmailMessage).where(EmailMessage.campaign_id == campaign.id,
                                              EmailMessage.status.in_((MS.PENDING_APPROVAL, MS.SCHEDULED)))
                   .values(status=MS.CANCELLED, error="An email was edited after approval", updated_at=utcnow()))
        audit_service.record(db, "campaign.reopened", user=user, actor_type=ActorType.USER, entity_type="campaign",
                             entity_id=campaign.id, summary=f"Email edited after approval; '{campaign.name}' needs approval again")


def list_templates(db: Session, campaign_id: int | None, kind: EmailKind | None) -> list[EmailTemplate]:
    q = select(EmailTemplate).where(EmailTemplate.deleted_at.is_(None))
    if campaign_id is not None:
        q = q.where(EmailTemplate.campaign_id == campaign_id)
    if kind:
        q = q.where(EmailTemplate.kind == kind)
    return list(db.scalars(q.order_by(EmailTemplate.campaign_id.is_(None), EmailTemplate.campaign_id,
                                      EmailTemplate.sequence_order, EmailTemplate.id)))


def create(db: Session, user: User, data: dict[str, Any]) -> EmailTemplate:
    campaign = _campaign(db, data.get("campaign_id"))
    _guard_campaign_edit(db, user, campaign)
    kind = data.get("kind") or (EmailKind.SEQUENCE_STEP if campaign else EmailKind.TEMPLATE)
    if campaign and data.get("sequence_order") is None:
        last = db.scalar(select(func.max(EmailTemplate.sequence_order)).where(
            EmailTemplate.campaign_id == campaign.id, EmailTemplate.deleted_at.is_(None))) or 0
        data["sequence_order"] = last + 1
    t = EmailTemplate(
        name=data["name"], subject=data["subject"], body_text=data["body_text"], kind=kind,
        campaign_id=campaign.id if campaign else None,
        client_id=data.get("client_id") or (campaign.client_id if campaign else None),
        sequence_order=data.get("sequence_order"), delay_days=data.get("delay_days"), status=ContentStatus.DRAFT,
    )
    db.add(t)
    db.flush()
    audit_service.record(db, "email.created", user=user, actor_type=ActorType.USER, entity_type="email_template",
                         entity_id=t.id, summary=f"Email draft '{t.name}' created")
    return t


def update(db: Session, user: User, t: EmailTemplate, changes: dict[str, Any]) -> EmailTemplate:
    changes = {k: v for k, v in changes.items() if v is not None or k in ("delay_days", "sequence_order")}
    if "campaign_id" in changes and changes["campaign_id"] != t.campaign_id:
        raise AppError("An email cannot be moved to another campaign; duplicate it instead")
    _guard_campaign_edit(db, user, _campaign(db, t.campaign_id))
    for k, v in changes.items():
        if k in ("name", "subject", "body_text", "kind", "sequence_order", "delay_days", "client_id"):
            setattr(t, k, v)
    t.status = ContentStatus.DRAFT
    return t


def delete(db: Session, user: User, t: EmailTemplate) -> None:
    _guard_campaign_edit(db, user, _campaign(db, t.campaign_id))
    t.deleted_at = utcnow()
    audit_service.record(db, "email.deleted", user=user, actor_type=ActorType.USER, entity_type="email_template",
                         entity_id=t.id, summary=f"Email draft '{t.name}' deleted")


# -- personalisation ---------------------------------------------------------------------------


def public_links(settings: Settings) -> bool:
    """True when PUBLIC_BASE_URL can be reached by recipients (not localhost / a private address)."""
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(settings.public_base_url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".test")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def unsubscribe_targets(settings: Settings, email: str) -> tuple[str | None, str | None]:
    """(one-click URL, mailto). Without a public URL, opt-outs arrive as replies to SENDER_EMAIL."""
    from urllib.parse import quote, urlencode

    mailto = f"mailto:{settings.sender_email}?subject={quote('Unsubscribe')}" if settings.sender_email else None
    if not public_links(settings):
        return None, mailto
    token = compliance.unsubscribe_token(email, settings.secret_key)
    return f"{settings.public_base_url.rstrip('/')}/unsubscribe?{urlencode({'e': email, 't': token})}", mailto


def agency_values(settings: Settings, recipient_email: str | None) -> dict[str, str | None]:
    unsub = None
    if recipient_email:
        url, mailto = unsubscribe_targets(settings, recipient_email)
        unsub = url or mailto
    return {"sender_name": settings.sender_name or None, "sender_email": settings.sender_email or None,
            "agency_name": settings.agency_name or None, "agency_address": settings.agency_address or None,
            "unsubscribe_link": unsub}


def preview(db: Session, settings: Settings, t: EmailTemplate, prospect_id: int | None) -> dict[str, Any]:
    values: dict[str, str | None] = {}
    email = None
    if prospect_id is not None:
        p = db.get(Prospect, prospect_id)
        if p is None or p.deleted_at is not None:
            raise NotFoundError("Prospect not found")
        values.update(compliance.prospect_values(p))
        email = p.email
    values.update(agency_values(settings, email))
    subject, m1 = compliance.render(t.subject, values)
    body, m2 = compliance.render(t.body_text, values)
    missing = list(dict.fromkeys(m1 + m2))
    suppressed = bool(email and is_suppressed(db, email))
    return {"subject": subject, "body_text": body, "missing_fields": missing, "recipient_email": email,
            "recipient_suppressed": suppressed}


# -- suppression & unsubscribe -----------------------------------------------------------------


def is_suppressed(db: Session, email: str) -> bool:
    return db.scalar(select(EmailSuppression.id).where(EmailSuppression.email == email.strip().lower())) is not None


def suppressed_set(db: Session) -> set[str]:
    return set(db.scalars(select(EmailSuppression.email)))


def add_suppression(db: Session, email: str, reason: SuppressionReason, source: str | None,
                    user: User | None = None) -> EmailSuppression:
    email = email.strip().lower()
    existing = db.scalar(select(EmailSuppression).where(EmailSuppression.email == email))
    if existing:
        # An unsubscribe or complaint always wins over a weaker reason.
        if reason in PROTECTED_REASONS and existing.reason not in PROTECTED_REASONS:
            existing.reason = reason
            existing.source = source
        return existing
    s = EmailSuppression(email=email, reason=reason, source=source)
    db.add(s)
    db.flush()
    audit_service.record(db, "email.suppressed", user=user, actor_type=ActorType.USER if user else ActorType.SYSTEM,
                         actor_label=None if user else "Unsubscribe link", entity_type="email_suppression",
                         entity_id=s.id, summary=f"Suppressed an address ({reason.value.lower()})")
    return s


def remove_suppression(db: Session, user: User, suppression_id: int) -> None:
    s = db.get(EmailSuppression, suppression_id)
    if s is None:
        raise NotFoundError("Suppression entry not found")
    if s.reason in PROTECTED_REASONS:
        raise PermissionDeniedError("Unsubscribes and complaints cannot be removed; the person opted out")
    db.delete(s)
    audit_service.record(db, "email.unsuppressed", user=user, actor_type=ActorType.USER,
                         entity_type="email_suppression", entity_id=suppression_id,
                         summary="Removed a manual suppression entry")


def list_suppressions(db: Session, q: str | None, offset: int, limit: int) -> tuple[list[EmailSuppression], int]:
    query = select(EmailSuppression)
    if q:
        query = query.where(EmailSuppression.email.contains(q.strip().lower()))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    return list(db.scalars(query.order_by(EmailSuppression.id.desc()).offset(offset).limit(limit))), total


def unsubscribe(db: Session, settings: Settings, email: str, token: str) -> bool:
    try:
        email = normalize_email(email) or ""
    except ValueError:
        return False
    if not email or not compliance.unsubscribe_valid(email, token, settings.secret_key):
        return False
    add_suppression(db, email, SuppressionReason.UNSUBSCRIBED, "unsubscribe link")
    return True


# -- audience ----------------------------------------------------------------------------------


def check_recipients(db: Session, emails: list[str]) -> dict[str, Any]:
    suppressed = suppressed_set(db)
    seen: set[str] = set()
    ok, invalid, dupes, blocked = [], [], [], []
    for raw in emails:
        try:
            e = normalize_email(raw)
        except ValueError:
            invalid.append(raw)
            continue
        if not e:
            continue
        if e in seen:
            dupes.append(e)
            continue
        seen.add(e)
        (blocked if e in suppressed else ok).append(e)
    return {"eligible": ok, "suppressed": blocked, "invalid": invalid, "duplicates": dupes}


def segment_prospects(db: Session, audience: dict[str, Any]) -> dict[str, Any]:
    """Prospects matching the filter; ``eligible`` have an email address that is not suppressed."""
    q = select(Prospect).where(Prospect.deleted_at.is_(None))
    if audience.get("stages"):
        q = q.where(Prospect.stage.in_(audience["stages"]))
    if audience.get("industry"):
        q = q.where(func.lower(Prospect.industry) == audience["industry"].strip().lower())
    if audience.get("city"):
        q = q.where(func.lower(Prospect.city) == audience["city"].strip().lower())
    if audience.get("min_score") is not None:
        q = q.where(Prospect.lead_score >= audience["min_score"])
    rows = list(db.scalars(q.order_by(Prospect.id)))
    suppressed = suppressed_set(db)
    with_email = [p for p in rows if p.email]
    eligible = [p for p in with_email if p.email.lower() not in suppressed]
    return {"matched": len(rows), "with_email": len(with_email), "suppressed": len(with_email) - len(eligible),
            "eligible": eligible}


def segment(db: Session, audience: dict[str, Any]) -> dict[str, Any]:
    """Count prospects matching the filter and how many can lawfully be emailed (not suppressed)."""
    seg = segment_prospects(db, audience)
    eligible = seg["eligible"]
    return {
        "matched": seg["matched"], "with_email": seg["with_email"],
        "suppressed": seg["suppressed"], "eligible": len(eligible),
        "sample": [{"id": p.id, "company_name": p.company_name, "city": p.city} for p in eligible[:10]],
    }
