"""Delivering one email over SMTP.

Kept deliberately small: builds a plain-text RFC 5322 message with the headers mailbox providers
expect from bulk senders (List-Unsubscribe, one-click unsubscribe per RFC 8058, Message-ID) and hands
it to an SMTP server. Errors are classified so the sender knows whether to retry, give up on one
recipient, or stop altogether (bad login / server unreachable for everyone).
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Protocol

from app.config.settings import Settings


class SendError(Exception):
    """Base class. ``str(err)`` is safe to store and show (no credentials)."""


class TransientSendError(SendError):
    """Try this message again later (4xx reply, timeout, connection dropped)."""


class RecipientRejected(SendError):
    """The server refused this recipient permanently (5xx on RCPT): treat as a bounce."""


class TransportUnavailable(SendError):
    """Nothing can be sent right now (not configured, login refused, server unreachable)."""


@dataclass(frozen=True)
class OutgoingEmail:
    to_email: str
    subject: str
    body_text: str
    unsubscribe_url: str | None = None  # https URL supporting one-click POST
    unsubscribe_mailto: str | None = None


class EmailTransport(Protocol):
    def send(self, email: OutgoingEmail) -> str:
        """Deliver the email; return its Message-ID. Raise a SendError subclass on failure."""
        ...


def sender_domain(settings: Settings) -> str:
    return settings.sender_email.rsplit("@", 1)[-1] if "@" in settings.sender_email else "agencyos.local"


def build_mime(settings: Settings, email: OutgoingEmail) -> MimeMessage:
    msg = MimeMessage()
    msg["From"] = formataddr((settings.sender_name or settings.agency_name or "", settings.sender_email))
    msg["To"] = email.to_email
    msg["Subject"] = email.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender_domain(settings))
    targets = []
    if email.unsubscribe_url:
        targets.append(f"<{email.unsubscribe_url}>")
    if email.unsubscribe_mailto:
        targets.append(f"<{email.unsubscribe_mailto}>")
    if targets:
        msg["List-Unsubscribe"] = ", ".join(targets)
        if email.unsubscribe_url:
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["Auto-Submitted"] = "auto-generated"  # discourages vacation auto-replies
    msg.set_content(email.body_text)
    return msg


class SmtpTransport:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _connect(self) -> smtplib.SMTP:
        s = self.settings
        if not (s.smtp_host and s.sender_email):
            raise TransportUnavailable("SMTP is not configured (SMTP_HOST and SENDER_EMAIL are required)")
        context = ssl.create_default_context()
        try:
            if s.smtp_security == "ssl":
                conn: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds,
                                                      context=context)
            else:
                conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
                if s.smtp_security == "starttls":
                    conn.starttls(context=context)
            if s.smtp_username:
                conn.login(s.smtp_username, s.smtp_password)
            return conn
        except smtplib.SMTPAuthenticationError as err:
            raise TransportUnavailable(f"The SMTP server refused the login ({err.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as err:
            raise TransportUnavailable(f"Cannot connect to the SMTP server: {type(err).__name__}") from None

    def send(self, email: OutgoingEmail) -> str:
        msg = build_mime(self.settings, email)
        conn = self._connect()
        try:
            conn.send_message(msg)
        except smtplib.SMTPRecipientsRefused as err:
            code = next(iter(err.recipients.values()))[0] if err.recipients else 550
            if 400 <= code < 500:
                raise TransientSendError(f"Recipient temporarily refused ({code})") from None
            raise RecipientRejected(f"Recipient address rejected ({code})") from None
        except smtplib.SMTPResponseException as err:
            if 400 <= err.smtp_code < 500:
                raise TransientSendError(f"Server asked to retry later ({err.smtp_code})") from None
            raise RecipientRejected(f"Message rejected ({err.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as err:
            raise TransientSendError(f"Connection problem while sending: {type(err).__name__}") from None
        finally:
            try:
                conn.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return str(msg["Message-ID"])


def default_transport(settings: Settings) -> EmailTransport | None:
    """The configured transport, or None when sending is turned off."""
    return SmtpTransport(settings) if settings.email_transport == "smtp" else None


def get_email_transport() -> EmailTransport | None:
    """FastAPI dependency (tests override it with a fake transport)."""
    from app.config.settings import get_settings

    return default_transport(get_settings())
