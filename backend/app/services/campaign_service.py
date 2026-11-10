"""Campaigns: drafts, AI-assisted content, audience, approval gate, manual status and metrics.

Status rules (enforced here, not by agents):
DRAFT → AWAITING_APPROVAL (request) → APPROVED | back to DRAFT (rejected/expired).
APPROVED → ACTIVE (marked as launched outside AgencyOS) ⇄ PAUSED → COMPLETED. Any open state → CANCELLED.
A budget change on an approved/running campaign needs its own CHANGE_BUDGET approval.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import approvals as gate
from app.config.settings import Settings, get_settings
from app.marketing import compliance
from app.models.approvals import Approval
from app.models.crm import Client
from app.models.enums import (
    ActorType, ApprovalActionType, ApprovalStatus, CampaignStatus as CS, CampaignType as CT, ContentStatus, EmailKind,
)
from app.models.identity import User
from app.models.marketing import Campaign, EmailTemplate
from app.services import audit_service, drafting, email_sending, email_service
from app.utils.errors import AppError, NotFoundError
from app.utils.time import utcnow

EMAIL_TYPES = {CT.COLD_EMAIL, CT.PROMOTIONAL_EMAIL, CT.NEWSLETTER, CT.LEAD_NURTURING, CT.REENGAGEMENT}
AD_TYPES = {CT.GOOGLE_ADS, CT.META_ADS}
FIELDS = ["name", "client_id", "target_audience", "objectives", "budget", "start_date", "end_date"]
MANUAL_TRANSITIONS = {
    CS.APPROVED: {CS.ACTIVE, CS.CANCELLED},
    CS.ACTIVE: {CS.PAUSED, CS.COMPLETED, CS.CANCELLED},
    CS.PAUSED: {CS.ACTIVE, CS.COMPLETED, CS.CANCELLED},
    CS.DRAFT: {CS.CANCELLED},
    CS.AWAITING_APPROVAL: set(),
    CS.COMPLETED: set(),
    CS.CANCELLED: set(),
}
CONSENT_LABELS = {
    "existing_customers": "Existing customers with an ongoing relationship",
    "opted_in": "Contacts who opted in to receive emails",
    "b2b_cold_outreach": "Business contacts (B2B cold outreach with opt-out)",
}


def _money(v: Any) -> str | None:
    return None if v is None else f"{Decimal(str(v)).quantize(Decimal('0.01'))}"


def get(db: Session, campaign_id: int) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Campaign not found")
    return c


def _client(db: Session, client_id: int | None) -> Client | None:
    if client_id is None:
        return None
    c = db.get(Client, client_id)
    if c is None or c.deleted_at is not None:
        raise NotFoundError("Client not found")
    return c


def _audit(db: Session, user: User | None, c: Campaign, action: str, summary: str) -> None:
    audit_service.record(db, action, user=user, actor_type=ActorType.USER if user else ActorType.SYSTEM,
                         entity_type="campaign", entity_id=c.id, summary=summary)


def derived_metrics(m: dict[str, Any]) -> dict[str, Any]:
    def rate(a: str, b: str) -> float | None:
        num, den = m.get(a), m.get(b)
        return round(num * 100 / den, 1) if num is not None and den else None

    spend = Decimal(str(m["spend"])) if m.get("spend") is not None else None
    leads = m.get("leads")
    return {
        "open_rate": rate("opens", "delivered") if m.get("delivered") else rate("opens", "sent"),
        "click_rate": rate("clicks", "delivered") if m.get("delivered") else rate("clicks", "sent"),
        "reply_rate": rate("replies", "delivered") if m.get("delivered") else rate("replies", "sent"),
        "ctr": rate("clicks", "impressions"),
        "conversion_rate": rate("conversions", "leads"),
        "cost_per_lead": _money(spend / leads) if spend is not None and leads else None,
    }


def serialize(db: Session, c: Campaign, *, full: bool = False) -> dict[str, Any]:
    client = db.get(Client, c.client_id) if c.client_id else None
    content = c.generated_content or {}
    emails = db.scalar(select(func.count()).select_from(EmailTemplate).where(
        EmailTemplate.campaign_id == c.id, EmailTemplate.deleted_at.is_(None))) or 0
    out = {
        "id": c.id, "name": c.name, "campaign_type": c.campaign_type.value, "client_id": c.client_id,
        "client_name": client.company_name if client else None, "target_audience": c.target_audience,
        "objectives": c.objectives, "budget": _money(c.budget), "start_date": c.start_date, "end_date": c.end_date,
        "status": c.status.value, "is_email": c.campaign_type in EMAIL_TYPES, "is_ads": c.campaign_type in AD_TYPES,
        "email_count": emails, "metrics": c.metrics or {}, "derived_metrics": derived_metrics(c.metrics or {}),
        "created_at": c.created_at, "updated_at": c.updated_at,
    }
    if full:
        pending = gate.pending_for(db, "campaign", c.id)
        out.update(
            drafts=content.get("drafts", []),
            audience=content.get("audience"),
            audience_summary=email_service.segment(db, content["audience"]) if content.get("audience") else None,
            emails=[email_service.serialize(t) for t in email_service.list_templates(db, c.id, None)],
            approvals=[{"id": a.id, "action_type": a.action_type.value, "status": a.status.value, "title": a.title,
                        "comments": a.comments, "decided_at": a.decided_at, "created_at": a.created_at,
                        "consent_basis": (a.payload or {}).get("consent_basis")}
                       for a in gate.history_for(db, "campaign", c.id)],
            pending_approval_id=pending.id if pending else None,
            ai_tasks=[drafting.task_brief(t) for t in drafting.linked_tasks(db, "campaign", c.id)],
            allowed_status_changes=sorted(s.value for s in MANUAL_TRANSITIONS[c.status]),
            readiness=readiness(db, c),
            outbox=email_sending.counts(db, c.id) if c.campaign_type in EMAIL_TYPES else None,
        )
    return out


def search(db: Session, *, q: str | None, status: CS | None, campaign_type: CT | None, client_id: int | None,
           offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    query = select(Campaign).where(Campaign.deleted_at.is_(None))
    if q:
        query = query.where(func.lower(Campaign.name).like(f"%{q.strip().lower()}%"))
    if status:
        query = query.where(Campaign.status == status)
    if campaign_type:
        query = query.where(Campaign.campaign_type == campaign_type)
    if client_id:
        query = query.where(Campaign.client_id == client_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Campaign.id.desc()).offset(offset).limit(limit))
    return [serialize(db, c) for c in rows], total


def _check_dates(c: Campaign) -> None:
    if c.start_date and c.end_date and c.end_date < c.start_date:
        raise AppError("End date cannot be before the start date")


def create(db: Session, user: User, data: dict[str, Any]) -> Campaign:
    _client(db, data.get("client_id"))
    c = Campaign(campaign_type=data["campaign_type"], status=CS.DRAFT,
                 **{k: v for k, v in data.items() if k in FIELDS and v is not None})
    _check_dates(c)
    db.add(c)
    db.flush()
    _audit(db, user, c, "campaign.created", f"Campaign '{c.name}' drafted")
    return c


def update(db: Session, user: User, c: Campaign, changes: dict[str, Any]) -> Campaign:
    changes = {k: v for k, v in changes.items() if k in FIELDS and not (k == "name" and v is None)}
    if "client_id" in changes:
        _client(db, changes["client_id"])
    if c.status in (CS.COMPLETED, CS.CANCELLED):
        raise AppError(f"A {c.status.value.lower()} campaign cannot be edited")
    if c.status == CS.AWAITING_APPROVAL:
        raise AppError("The campaign is awaiting approval; wait for the decision before editing")
    budget_changed = "budget" in changes and _money(changes["budget"]) != _money(c.budget)
    changed = [k for k, v in changes.items() if getattr(c, k) != v and k != "budget"] + (["budget"] if budget_changed else [])
    if not changed:
        return c
    if c.status in (CS.APPROVED, CS.ACTIVE, CS.PAUSED):
        if changed != ["budget"]:
            raise AppError("Only the budget can change after approval, and it needs a new approval")
        old = _money(c.budget)
        c.budget = changes["budget"]
        approval = gate.request(
            db, user=user, action_type=ApprovalActionType.CHANGE_BUDGET,
            title=f"Change budget of '{c.name}' from {old or 'none'} to {_money(c.budget)}",
            entity_type="campaign", entity_id=c.id, client_id=c.client_id,
            details=f"Budget change requested while the campaign is {c.status.value.lower()}.",
            extra={"previous_status": c.status.value, "previous_budget": old, "new_budget": _money(c.budget)},
        )
        c.status = CS.AWAITING_APPROVAL
        _audit(db, user, c, "campaign.budget_change_requested", f"Budget change for '{c.name}' sent for approval (#{approval.id})")
        return c
    for k in changed:
        setattr(c, k, changes[k])
    _check_dates(c)
    _audit(db, user, c, "campaign.updated", f"Updated '{c.name}': {', '.join(changed)}")
    return c


def delete(db: Session, user: User, c: Campaign) -> None:
    if c.status in (CS.ACTIVE, CS.AWAITING_APPROVAL):
        raise AppError("Pause or wait for the approval decision before deleting this campaign")
    c.deleted_at = utcnow()
    _audit(db, user, c, "campaign.deleted", f"Campaign '{c.name}' deleted")


def set_audience(db: Session, user: User, c: Campaign, audience: dict[str, Any]) -> dict[str, Any]:
    if c.status not in (CS.DRAFT,):
        raise AppError("The audience can only change while the campaign is a draft")
    c.generated_content = {**(c.generated_content or {}), "audience": jsonable_encoder(audience)}
    _audit(db, user, c, "campaign.audience_set", f"Audience updated for '{c.name}'")
    return email_service.segment(db, audience)


# -- approval ----------------------------------------------------------------------------------


def readiness(db: Session, c: Campaign) -> list[dict[str, str]]:
    """What still blocks an approval request. Empty list = ready."""
    problems: list[dict[str, str]] = []
    if c.campaign_type in EMAIL_TYPES:
        emails = email_service.list_templates(db, c.id, None)
        if not emails:
            problems.append({"level": "error", "message": "Add at least one email draft"})
        for t in emails:
            errs = [i["message"] for i in compliance.check_email(t.subject, t.body_text) if i["level"] == "error"]
            if errs:
                problems.append({"level": "error", "message": f"'{t.name}': " + "; ".join(errs)})
        audience = (c.generated_content or {}).get("audience")
        if not audience:
            problems.append({"level": "error", "message": "Choose an audience segment"})
        elif email_service.segment(db, audience)["eligible"] == 0:
            problems.append({"level": "error", "message": "The audience has no eligible (non-suppressed) email addresses"})
    if c.campaign_type in AD_TYPES and not c.budget:
        problems.append({"level": "error", "message": "Set a budget for the ad campaign"})
    if not (c.objectives or "").strip():
        problems.append({"level": "warning", "message": "No objectives written"})
    return problems


def request_approval(db: Session, user: User, c: Campaign, data: dict[str, Any],
                     settings: Settings | None = None) -> Approval:
    if c.status != CS.DRAFT:
        raise AppError("Only draft campaigns can be sent for approval")
    blocking = [p["message"] for p in readiness(db, c) if p["level"] == "error"]
    if blocking:
        raise AppError("The campaign is not ready for approval", details=blocking)
    extra: dict[str, Any] = {"campaign_type": c.campaign_type.value}
    if c.campaign_type in EMAIL_TYPES:
        if not data.get("consent_basis"):
            raise AppError("Select the consent basis for this email audience")
        if not data.get("confirm_compliance"):
            raise AppError("Confirm that sender details, postal address and unsubscribe are in every email")
        audience = (c.generated_content or {}).get("audience") or {}
        # Render every email for every recipient now: the approver reviews exactly what will be sent.
        outbox = email_sending.build_outbox(db, settings or get_settings(), c, audience)
        extra.update(consent_basis=data["consent_basis"], audience=audience,
                     eligible_recipients=outbox["recipients"], suppressed_excluded=outbox["suppressed_excluded"],
                     messages=outbox["messages"], incomplete_excluded=outbox["incomplete_excluded"])
        action = ApprovalActionType.SEND_EMAIL

        def count(n: int, one: str, many: str) -> str:
            return f"{n} {one if n == 1 else many}"

        details = (f"Send {count(outbox['messages'], 'email', 'emails')} to "
                   f"{count(outbox['recipients'], 'recipient', 'recipients')} "
                   f"({count(outbox['steps'], 'step', 'steps')} each; "
                   f"{count(outbox['suppressed_excluded'], 'do-not-contact address', 'do-not-contact addresses')} "
                   f"excluded")
        if outbox["incomplete_excluded"]:
            details += (f"; {count(outbox['incomplete_excluded'], 'recipient', 'recipients')} left out for missing "
                        f"details, e.g. {outbox['incomplete_examples'][0]}")
        details += (f"). Consent basis: {CONSENT_LABELS[data['consent_basis']]}. Every message can be read "
                    f"on the campaign's Outbox before deciding.")
    elif c.campaign_type in AD_TYPES:
        action = ApprovalActionType.LAUNCH_ADS
        details = f"Launch {c.campaign_type.value.replace('_', ' ').title()} with budget {_money(c.budget)}."
    else:
        action = ApprovalActionType.OTHER
        details = "Run this campaign."
    if data.get("notes"):
        details += f" Notes: {data['notes']}"
    approval = gate.request(db, user=user, action_type=action, title=f"Launch campaign '{c.name}'",
                            entity_type="campaign", entity_id=c.id, client_id=c.client_id, details=details, extra=extra)
    c.status = CS.AWAITING_APPROVAL
    for t in email_service.list_templates(db, c.id, None):
        t.status = ContentStatus.AWAITING_APPROVAL
    if action == ApprovalActionType.SEND_EMAIL:
        email_sending.attach_approval(db, c, approval.id)
    return approval


def _on_decided(db: Session, approval: Approval) -> None:
    c = db.get(Campaign, (approval.payload or {}).get("entity_id"))
    if c is None or c.status != CS.AWAITING_APPROVAL:
        return
    approved = approval.status == ApprovalStatus.APPROVED
    if approval.action_type == ApprovalActionType.CHANGE_BUDGET:
        previous = CS((approval.payload or {}).get("previous_status") or CS.APPROVED.value)
        if not approved:
            prev_budget = (approval.payload or {}).get("previous_budget")
            c.budget = Decimal(prev_budget) if prev_budget else None
        c.status = previous
    else:
        c.status = CS.APPROVED if approved else CS.DRAFT
        for t in email_service.list_templates(db, c.id, None):
            t.status = ContentStatus.APPROVED if approved else ContentStatus.DRAFT
        if approval.action_type == ApprovalActionType.SEND_EMAIL:
            email_sending.on_approval_settled(db, c, approval.status)
    _audit(db, None, c, "campaign.approval_settled",
           f"'{c.name}' {approval.action_type.value.lower()} {approval.status.value.lower()}; now {c.status.value.lower()}")


gate.register("campaign", _on_decided)


def change_status(db: Session, user: User, c: Campaign, new: CS, note: str | None) -> Campaign:
    if new not in MANUAL_TRANSITIONS[c.status]:
        raise AppError(f"A {c.status.value.lower()} campaign cannot be marked {new.value.lower()}")
    old = c.status
    sends = c.campaign_type in EMAIL_TYPES and email_sending.counts(db, c.id).get("SCHEDULED", 0) > 0
    c.status = new
    email_sending.on_campaign_status(db, c, new)
    if new == CS.ACTIVE:
        suffix = (" (approved emails will now be sent)" if sends
                  else " (launched outside AgencyOS; nothing was sent by this app)")
    else:
        suffix = ""
    _audit(db, user, c, "campaign.status", f"'{c.name}' {old.value} → {new.value}{suffix}" + (f". {note}" if note else ""))
    return c


def update_metrics(db: Session, user: User, c: Campaign, metrics: dict[str, Any]) -> Campaign:
    merged = {**(c.metrics or {}), **jsonable_encoder({k: v for k, v in metrics.items() if v is not None})}
    if merged.get("opens") is not None and merged.get("sent") is not None and merged["opens"] > merged["sent"] * 10:
        raise AppError("Opens look implausible compared with emails sent")
    c.metrics = {**merged, "updated_at": utcnow().isoformat()}
    _audit(db, user, c, "campaign.metrics", f"Metrics updated for '{c.name}'")
    return c


# -- AI drafting -------------------------------------------------------------------------------

_KIND = {
    "strategy": ("marketing", "Write a campaign strategy: objective, audience, channels, messaging, timeline and KPIs."),
    "emails": ("email_marketing", "Write the email sequence for this campaign. Return one deliverable per email "
               "(kind email_draft) whose content starts with 'Subject: ...' on the first line. Every email must "
               "include {{sender_name}}, {{agency_address}} and {{unsubscribe_link}}. Use merge fields such as "
               "{{first_name}} and {{company_name}}; never invent recipient details."),
    "ads": ("advertising", "Write ad copy variations (headlines and descriptions) and targeting suggestions."),
    "audience": ("marketing", "Recommend audience segments and targeting for this campaign, based only on the "
                 "data provided."),
}


def generate(db: Session, user: User, c: Campaign, kind: str, notes: str | None):
    if c.status not in (CS.DRAFT,) and kind != "audience":
        raise AppError("New drafts can only be generated while the campaign is a draft")
    agent_key, instructions = _KIND[kind]
    client = db.get(Client, c.client_id) if c.client_id else None
    context = jsonable_encoder({
        "campaign": {k: v for k, v in serialize(db, c).items()
                     if k in ("name", "campaign_type", "target_audience", "objectives", "budget", "start_date", "end_date")},
        "client": {"company_name": client.company_name, "industry": client.industry,
                   "service_package": client.service_package} if client else None,
        "audience_summary": email_service.segment(db, (c.generated_content or {})["audience"])
        if (c.generated_content or {}).get("audience") else None,
    })
    if notes:
        instructions += f"\n\nFounder's notes: {notes}"
    return drafting.start(db, user, agent_key=agent_key, title=f"{c.name}: {kind} draft", instructions=instructions,
                          context=context, task_type="campaign_generate", entity_type="campaign", entity_id=c.id)


def analyze(db: Session, user: User, c: Campaign):
    if not c.metrics:
        raise AppError("Enter some performance metrics first")
    context = jsonable_encoder({"campaign": serialize(db, c)})
    return drafting.start(db, user, agent_key="analytics", title=f"{c.name}: performance analysis",
                          instructions="Analyse this campaign's performance from the metrics provided and suggest "
                                       "concrete optimisations. Use only the figures given.",
                          context=context, task_type="campaign_analyze", entity_type="campaign", entity_id=c.id)


def import_task(db: Session, user: User, c: Campaign, task_id: int, indexes: list[int] | None) -> dict[str, Any]:
    task, deliverables = drafting.finished_deliverables(db, task_id, "campaign", c.id)
    chosen = [d for i, d in enumerate(deliverables) if indexes is None or i in indexes]
    if not chosen:
        raise AppError("Nothing selected to import")
    emails = [d for d in chosen if d.get("kind") == "email_draft"]
    others = [d for d in chosen if d.get("kind") != "email_draft"]
    if emails:
        _guard = email_service._guard_campaign_edit  # same edit rules as manual email changes
        _guard(db, user, c)
        for i, d in enumerate(emails):
            subject, body = drafting.split_email(d)
            email_service.create(db, user, {"name": d.get("title") or f"Email {i + 1}", "subject": subject,
                                            "body_text": body, "campaign_id": c.id, "kind": EmailKind.SEQUENCE_STEP,
                                            "delay_days": drafting.guess_delay(d.get("title", ""), i)})
    if others:
        drafts = list((c.generated_content or {}).get("drafts", []))
        drafts += [{"title": d.get("title"), "kind": d.get("kind"), "content": d.get("content"),
                    "source_task_id": task.id, "imported_at": utcnow().isoformat()} for d in others]
        c.generated_content = {**(c.generated_content or {}), "drafts": drafts}
    _audit(db, user, c, "campaign.imported", f"Imported {len(chosen)} draft(s) from AI task #{task.id} into '{c.name}'")
    return {"emails_created": len(emails), "drafts_saved": len(others)}


def remove_draft(db: Session, user: User, c: Campaign, index: int) -> None:
    drafts = list((c.generated_content or {}).get("drafts", []))
    if not 0 <= index < len(drafts):
        raise NotFoundError("Draft not found")
    drafts.pop(index)
    c.generated_content = {**(c.generated_content or {}), "drafts": drafts}
