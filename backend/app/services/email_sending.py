"""Sending approved campaign emails (Phase 10).

Lifecycle of one message (``EmailMessage``):

* **Approval requested** for an email campaign: every recipient × sequence step is rendered now and
  frozen as PENDING_APPROVAL. The approver reviews exactly these rows. Recipients without an email
  address, suppressed, or whose email would still contain ``[missing: …]`` are left out and counted.
* **Approved** → SCHEDULED. **Rejected / expired** → CANCELLED. Nothing is sent yet.
* **Campaign started (ACTIVE)** → step-1 messages become due. Each later step becomes due
  ``delay_days`` after the previous step to that recipient was actually sent.
* **Sender** (``run_once``) sends due messages of ACTIVE campaigns only, one at a time, within the
  daily limit, re-checking the do-not-contact list and the prospect's stage right before each send.
  Pausing the campaign stops sending; cancelling or completing it cancels everything unsent.

No agent can reach this module: messages are created only from a person's approval request, and
only a Founder/Admin decision makes them sendable.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.integrations.email_transport import (
    EmailTransport,
    OutgoingEmail,
    RecipientRejected,
    SendError,
    TransientSendError,
    TransportUnavailable,
    default_transport,
)
from app.marketing import compliance
from app.models.crm import Prospect, ProspectActivity
from app.models.enums import (
    ActorType,
    ApprovalStatus,
    CampaignStatus,
    EmailMessageStatus as MS,
    SalesStage,
    SuppressionReason,
)
from app.models.identity import User
from app.models.marketing import Campaign, EmailMessage, EmailTemplate
from app.services import audit_service, email_service
from app.utils.errors import AppError
from app.utils.time import utcnow

log = logging.getLogger("agencyos.email")

RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
# Follow-ups stop once the prospect has engaged or the deal is decided.
STOP_STAGES = {SalesStage.RESPONDED, SalesStage.MEETING_SCHEDULED, SalesStage.PROPOSAL_SENT,
               SalesStage.NEGOTIATION, SalesStage.WON, SalesStage.LOST}
UNSENT = (MS.PENDING_APPROVAL, MS.SCHEDULED)

# Last outcome of the sender in this process (shown in Settings).
_state: dict[str, Any] = {"last_run_at": None, "last_error": None, "last_sent_at": None}
_state_lock = threading.Lock()


# -- building the outbox at approval time ------------------------------------------------------


def build_outbox(db: Session, settings: Settings, c: Campaign, audience: dict[str, Any]) -> dict[str, Any]:
    """Render and freeze every message for this campaign. Returns counts for the approval details."""
    # Anything left from an earlier (rejected) request is replaced.
    db.execute(update(EmailMessage).where(EmailMessage.campaign_id == c.id, EmailMessage.status.in_(UNSENT))
               .values(status=MS.CANCELLED, error="Replaced by a new approval request", updated_at=utcnow()))
    steps = [t for t in email_service.list_templates(db, c.id, None)]
    if not steps:
        raise AppError("Add at least one email to the campaign")
    seg = email_service.segment_prospects(db, audience)
    if len(seg["eligible"]) > settings.email_max_recipients:
        raise AppError(f"This audience has {len(seg['eligible'])} recipients; the limit per campaign is "
                       f"{settings.email_max_recipients} (EMAIL_MAX_RECIPIENTS). Narrow the audience.")
    created, incomplete, seen = 0, [], set()
    for p in seg["eligible"]:
        addr = p.email.strip().lower()
        if addr in seen:
            continue
        seen.add(addr)
        rendered = []
        for t in steps:
            values = {**compliance.prospect_values(p), **email_service.agency_values(settings, addr)}
            subject, m1 = compliance.render(t.subject, values)
            body, m2 = compliance.render(t.body_text, values)
            if m1 or m2:
                incomplete.append({"company_name": p.company_name, "missing": sorted(set(m1 + m2))})
                rendered = []
                break
            rendered.append((t, subject, body))
        for step, (t, subject, body) in enumerate(rendered, start=1):
            db.add(EmailMessage(campaign_id=c.id, template_id=t.id, prospect_id=p.id, step=step,
                                delay_days=max(0, t.delay_days or 0) if step > 1 else 0, to_email=addr,
                                subject=subject[:300], body_text=body, status=MS.PENDING_APPROVAL))
            created += 1
    db.flush()
    recipients = len(seen) - len(incomplete)
    if recipients == 0:
        reason = " (every recipient is missing a merge field)" if incomplete else ""
        raise AppError(f"No recipient can be emailed{reason}",
                       details=[f"{i['company_name']}: missing {', '.join(i['missing'])}" for i in incomplete[:10]])
    return {"recipients": recipients, "messages": created, "steps": len(steps),
            "suppressed_excluded": seg["suppressed"], "no_email": seg["matched"] - seg["with_email"],
            "incomplete_excluded": len(incomplete),
            "incomplete_examples": [f"{i['company_name']}: missing {', '.join(i['missing'])}" for i in incomplete[:5]]}


def attach_approval(db: Session, c: Campaign, approval_id: int) -> None:
    db.execute(update(EmailMessage).where(EmailMessage.campaign_id == c.id,
                                          EmailMessage.status == MS.PENDING_APPROVAL)
               .values(approval_id=approval_id))


def on_approval_settled(db: Session, c: Campaign, status: ApprovalStatus) -> None:
    approved = status == ApprovalStatus.APPROVED
    db.execute(update(EmailMessage).where(EmailMessage.campaign_id == c.id,
                                          EmailMessage.status == MS.PENDING_APPROVAL)
               .values(status=MS.SCHEDULED if approved else MS.CANCELLED,
                       error=None if approved else f"Approval {status.value.lower()}", updated_at=utcnow()))


def on_campaign_status(db: Session, c: Campaign, new: CampaignStatus) -> None:
    if new == CampaignStatus.ACTIVE:
        # Step 1 becomes due now; later steps get their time when the previous step is sent.
        db.execute(update(EmailMessage).where(EmailMessage.campaign_id == c.id, EmailMessage.status == MS.SCHEDULED,
                                              EmailMessage.step == 1, EmailMessage.send_after.is_(None))
                   .values(send_after=utcnow()))
    elif new in (CampaignStatus.CANCELLED, CampaignStatus.COMPLETED):
        db.execute(update(EmailMessage).where(EmailMessage.campaign_id == c.id, EmailMessage.status.in_(UNSENT))
                   .values(status=MS.CANCELLED, error=f"Campaign {new.value.lower()}", updated_at=utcnow()))


def counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(select(EmailMessage.status, func.count()).where(EmailMessage.campaign_id == campaign_id)
                      .group_by(EmailMessage.status)).all()
    return {s.value: n for s, n in rows}


def list_messages(db: Session, campaign_id: int, status: MS | None, offset: int, limit: int
                  ) -> tuple[list[dict[str, Any]], int]:
    q = select(EmailMessage, Prospect.company_name).outerjoin(Prospect, Prospect.id == EmailMessage.prospect_id) \
        .where(EmailMessage.campaign_id == campaign_id)
    if status:
        q = q.where(EmailMessage.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(EmailMessage.step, EmailMessage.id).offset(offset).limit(limit)).all()
    return [serialize(m, company) for m, company in rows], total


def serialize(m: EmailMessage, company_name: str | None = None) -> dict[str, Any]:
    return {"id": m.id, "campaign_id": m.campaign_id, "prospect_id": m.prospect_id, "company_name": company_name,
            "step": m.step, "delay_days": m.delay_days, "to_email": m.to_email, "subject": m.subject,
            "body_text": m.body_text, "status": m.status.value, "send_after": m.send_after, "sent_at": m.sent_at,
            "attempts": m.attempts, "error": m.error}


# -- sending -----------------------------------------------------------------------------------


def sent_last_24h(db: Session, now: datetime | None = None) -> int:
    since = (now or utcnow()) - timedelta(hours=24)
    return db.scalar(select(func.count()).select_from(EmailMessage)
                     .where(EmailMessage.status == MS.SENT, EmailMessage.sent_at >= since)) or 0


def _redirect(settings: Settings, email: OutgoingEmail) -> OutgoingEmail:
    if not settings.email_redirect_to:
        return email
    return OutgoingEmail(to_email=settings.email_redirect_to, subject=f"[Test → {email.to_email}] {email.subject}",
                         body_text=email.body_text, unsubscribe_url=email.unsubscribe_url,
                         unsubscribe_mailto=email.unsubscribe_mailto)


def _skip_reason(db: Session, m: EmailMessage) -> str | None:
    if email_service.is_suppressed(db, m.to_email):
        return "On the do-not-contact list"
    p = db.get(Prospect, m.prospect_id) if m.prospect_id else None
    if p is None or p.deleted_at is not None:
        return "Prospect was deleted"
    if (p.email or "").strip().lower() != m.to_email:
        return "Prospect's email address changed after approval"
    if m.step > 1 and p.stage in STOP_STAGES:
        return f"Prospect is now {p.stage.value.lower().replace('_', ' ')}; follow-ups stopped"
    return None


def _cascade_skip(db: Session, m: EmailMessage, why: str) -> None:
    db.execute(update(EmailMessage).where(EmailMessage.campaign_id == m.campaign_id,
                                          EmailMessage.to_email == m.to_email, EmailMessage.step > m.step,
                                          EmailMessage.status == MS.SCHEDULED)
               .values(status=MS.SKIPPED, error=why, updated_at=utcnow()))


def _after_sent(db: Session, m: EmailMessage, now: datetime) -> None:
    nxt = db.scalar(select(EmailMessage).where(EmailMessage.campaign_id == m.campaign_id,
                                               EmailMessage.to_email == m.to_email,
                                               EmailMessage.step == m.step + 1,
                                               EmailMessage.status == MS.SCHEDULED))
    if nxt is not None:
        nxt.send_after = now + timedelta(days=nxt.delay_days)
    p = db.get(Prospect, m.prospect_id) if m.prospect_id else None
    if p is not None:
        p.last_contacted_at = now
        if p.stage in (SalesStage.NEW, SalesStage.RESEARCHED, SalesStage.QUALIFIED):
            p.stage = SalesStage.CONTACTED
        c = db.get(Campaign, m.campaign_id)
        db.add(ProspectActivity(prospect_id=p.id, activity_type="email_sent",
                                description=f"Email {m.step} of campaign '{c.name if c else ''}' sent: {m.subject}"))


def _finish_campaigns(db: Session) -> None:
    for c in db.scalars(select(Campaign).where(Campaign.status == CampaignStatus.ACTIVE)):
        left = db.scalar(select(func.count()).select_from(EmailMessage).where(
            EmailMessage.campaign_id == c.id, EmailMessage.status.in_((MS.SCHEDULED, MS.SENDING))))
        total = db.scalar(select(func.count()).select_from(EmailMessage).where(EmailMessage.campaign_id == c.id))
        if total and not left:
            c.status = CampaignStatus.COMPLETED
            audit_service.record(db, "campaign.completed", actor_type=ActorType.SYSTEM, actor_label="Email sender",
                                 entity_type="campaign", entity_id=c.id,
                                 summary=f"'{c.name}': every approved email was sent or skipped; campaign completed")


def run_once(session_factory: Callable[[], Session], settings: Settings, transport: EmailTransport | None,
             *, max_messages: int = 10, sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """Send up to ``max_messages`` due messages. Safe to call from several processes (atomic claim)."""
    stats = {"sent": 0, "failed": 0, "skipped": 0, "retry": 0}
    with _state_lock:
        _state["last_run_at"] = utcnow()
    if transport is None:
        return stats
    for i in range(max_messages):
        with session_factory() as db:
            now = utcnow()
            if sent_last_24h(db, now) >= settings.email_daily_limit:
                with _state_lock:
                    _state["last_error"] = f"Daily limit of {settings.email_daily_limit} emails reached"
                break
            candidate = db.scalar(
                select(EmailMessage.id).join(Campaign, Campaign.id == EmailMessage.campaign_id)
                .where(EmailMessage.status == MS.SCHEDULED, EmailMessage.send_after <= now,
                       Campaign.status == CampaignStatus.ACTIVE)
                .order_by(EmailMessage.send_after, EmailMessage.id).limit(1))
            if candidate is None:
                _finish_campaigns(db)
                db.commit()
                break
            claimed = db.execute(update(EmailMessage).where(EmailMessage.id == candidate,
                                                            EmailMessage.status == MS.SCHEDULED)
                                 .values(status=MS.SENDING, updated_at=now)).rowcount
            db.commit()
            if not claimed:
                continue  # another sender took it
            m = db.get(EmailMessage, candidate)
            why = _skip_reason(db, m)
            if why:
                m.status, m.error = MS.SKIPPED, why
                _cascade_skip(db, m, "An earlier email to this recipient was not sent")
                stats["skipped"] += 1
                db.commit()
                continue
            url, mailto = email_service.unsubscribe_targets(settings, m.to_email)
            outgoing = _redirect(settings, OutgoingEmail(m.to_email, m.subject, m.body_text, url, mailto))
            m.attempts += 1
            try:
                m.provider_message_id = transport.send(outgoing)[:300]
            except TransportUnavailable as err:
                # Nobody can be emailed right now: put it back without using up an attempt.
                m.status, m.attempts, m.error = MS.SCHEDULED, m.attempts - 1, str(err)
                db.commit()
                with _state_lock:
                    _state["last_error"] = str(err)
                log.warning(f"Email sending paused: {err}")
                break
            except RecipientRejected as err:
                m.status, m.error = MS.FAILED, str(err)
                email_service.add_suppression(db, m.to_email, SuppressionReason.BOUNCED, "SMTP rejection")
                _cascade_skip(db, m, "An earlier email to this recipient bounced")
                stats["failed"] += 1
            except (TransientSendError, SendError) as err:
                if m.attempts > len(RETRY_DELAYS):
                    m.status, m.error = MS.FAILED, f"Gave up after {m.attempts} attempts: {err}"
                    _cascade_skip(db, m, "An earlier email to this recipient failed")
                    stats["failed"] += 1
                else:
                    m.status, m.error = MS.SCHEDULED, str(err)
                    m.send_after = now + RETRY_DELAYS[m.attempts - 1]
                    stats["retry"] += 1
            else:
                m.status, m.sent_at, m.error = MS.SENT, now, None
                _after_sent(db, m, now)
                stats["sent"] += 1
                with _state_lock:
                    _state["last_sent_at"], _state["last_error"] = now, None
            db.commit()
        if i + 1 < max_messages and settings.email_send_interval_seconds:
            sleep(settings.email_send_interval_seconds)
    if any(stats.values()):
        log.info(f"Email sender: {stats}")
    return stats


def recover_interrupted(db: Session) -> int:
    """Messages left SENDING by a crash: their outcome is unknown, so they are not re-sent automatically."""
    n = db.execute(update(EmailMessage).where(EmailMessage.status == MS.SENDING)
                   .values(status=MS.FAILED, error="Interrupted while sending; it may or may not have been "
                           "delivered, so it was not retried", updated_at=utcnow())).rowcount
    db.commit()
    return n


# -- status & test email -----------------------------------------------------------------------


def status(db: Session, settings: Settings) -> dict[str, Any]:
    with _state_lock:
        state = dict(_state)
    scheduled = db.scalar(select(func.count()).select_from(EmailMessage).where(EmailMessage.status == MS.SCHEDULED)) or 0
    configured = bool(settings.smtp_host and settings.sender_email)
    return {
        "transport": settings.email_transport, "enabled": settings.email_transport == "smtp" and configured,
        "configured": configured, "smtp_host": settings.smtp_host or None, "smtp_port": settings.smtp_port,
        "smtp_security": settings.smtp_security, "from": settings.sender_email or None,
        "from_name": settings.sender_name or settings.agency_name or None,
        "daily_limit": settings.email_daily_limit, "sent_last_24h": sent_last_24h(db), "scheduled": scheduled,
        "interval_seconds": settings.email_send_interval_seconds, "redirect_to": settings.email_redirect_to or None,
        "unsubscribe_mode": "link" if email_service.public_links(settings) else "reply",
        "last_run_at": state["last_run_at"], "last_sent_at": state["last_sent_at"], "last_error": state["last_error"],
    }


def send_test(db: Session, settings: Settings, user: User, transport: EmailTransport | None,
              template: EmailTemplate | None = None, prospect_id: int | None = None) -> dict[str, Any]:
    """Send one email to the signed-in user's own address (never to a prospect)."""
    if transport is None:
        raise AppError("Email sending is turned off. Set EMAIL_TRANSPORT=smtp and the SMTP settings in .env.")
    if sent_last_24h(db) >= settings.email_daily_limit:
        raise AppError(f"The daily limit of {settings.email_daily_limit} emails has been reached")
    if template is not None:
        prev = email_service.preview(db, settings, template, prospect_id)
        subject, body = f"[Test] {prev['subject']}", prev["body_text"]
    else:
        subject = "[Test] AgencyOS can send email"
        body = (f"This test was sent by AgencyOS through {settings.smtp_host}:{settings.smtp_port} "
                f"at {utcnow():%Y-%m-%d %H:%M} UTC.\n\nIf you can read this, outgoing email works.")
    url, mailto = email_service.unsubscribe_targets(settings, user.email)
    try:
        message_id = transport.send(OutgoingEmail(user.email, subject, body, url, mailto))
    except SendError as err:
        raise AppError(f"The test email was not sent: {err}") from None
    audit_service.record(db, "email.test_sent", user=user, actor_type=ActorType.USER,
                         entity_type="email_template" if template else "system",
                         entity_id=template.id if template else None, summary=f"Test email sent to {user.email}")
    return {"sent_to": user.email, "message_id": message_id}


# -- background loop ---------------------------------------------------------------------------


class EmailSender:
    """Background thread that calls ``run_once`` every ``poll_seconds``."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings, poll_seconds: float = 30.0,
                 transport_factory: Callable[[Settings], EmailTransport | None] = default_transport):
        self.session_factory, self.settings, self.poll_seconds = session_factory, settings, poll_seconds
        self.transport_factory = transport_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "EmailSender":
        try:
            with self.session_factory() as db:
                from sqlalchemy import inspect

                if not inspect(db.get_bind()).has_table("email_messages"):
                    log.warning("Email sender: database not initialised; run `python -m app.cli init-db`")
                    n = 0
                else:
                    n = recover_interrupted(db)
            if n:
                log.warning(f"{n} email(s) were interrupted mid-send and marked failed (not re-sent)")
        except Exception:  # noqa: BLE001 - e.g. database not initialised yet; never block startup
            log.exception("Email sender recovery failed")
        self._thread = threading.Thread(target=self._loop, name="email-sender", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_once(self.session_factory, self.settings, self.transport_factory(self.settings),
                         sleep=lambda s: self._stop.wait(s))
            except Exception:  # noqa: BLE001 - keep the loop alive; the error is logged
                log.exception("Email sender run failed")
            self._stop.wait(self.poll_seconds)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
