# AgencyOS — Security review (Phase 6)

Scope: the application code in `backend/app` and `frontend/assets/js`, its configuration and the
Docker image, as of Phase 6. Method: manual review against the OWASP Top 10 and ASVS level-1
topics, plus automated scans (Bandit for Python code, pip-audit for dependencies), both of which
also run in CI on every pull request.

## Results of automated scans

| Tool | Result |
|---|---|
| Bandit 1.9.4 (`-r backend/app`) | 3 low-severity `assert` statements; replaced with explicit errors. No medium/high findings |
| pip-audit 2.10.1 (`requirements.txt`) | No known vulnerabilities in the pinned dependencies |
| Frontend scan for `innerHTML`/`eval`/`document.write` | None; all text is set with `textContent` |

## Findings fixed in Phase 6

| # | Finding | Risk | Fix |
|---|---|---|---|
| 1 | Login was rate-limited per IP only; one account could be guessed from many IPs | Medium | Per-account throttle: 5 failed logins per 15 minutes, audited (`auth.login_throttled`); cleared on success |
| 2 | Fixed-window rate limiter allowed 2× bursts across minute boundaries | Low | Sliding-window limiter |
| 3 | No request-size limit before multipart parsing; large uploads were fully read before the CSV size check | Medium (DoS) | Requests over `MAX_REQUEST_BYTES` (5 MB) rejected with 413 before parsing; chunked bodies without a length refused (411) |
| 4 | `/docs` and `/openapi.json` public in production | Low (information) | Off by default in production (`ENABLE_API_DOCS` to override) |
| 5 | No HSTS when served over HTTPS | Low | `Strict-Transport-Security` when cookies are Secure |
| 6 | Behind a reverse proxy, every client had the proxy's IP (shared rate limit, wrong audit IPs) | Medium | Uvicorn `--proxy-headers` with `FORWARDED_ALLOW_IPS` (trusted proxy only) |
| 7 | Database constraint violations returned 500 | Low | Mapped to 409 without leaking SQL |
| 8 | Prospect and report CSV exports (personal data) were not audited | Low | `prospect.exported` / `report.exported` audit entries |
| 9 | No way to disable a departed user or reset a forgotten password | Medium | Deactivate/reactivate (UI + CLI) revokes all sessions; CLI password reset; last-Founder protection |
| 10 | Client-supplied `X-Request-ID` accepted any ASCII | Low (log hygiene) | Only `[A-Za-z0-9._-]{1,64}`; otherwise a new ID |
| 11 | Docker image ran the app with write access to its own code | Low | Code owned by root; only `data/` and `storage/` writable by the app user |
| 12 | Conditional UI could render the text "null" | UX bug | Empty children skipped app-wide |

## Controls in place (summary)

- **Authentication**: scrypt password hashing; strength rules; server-side sessions (only the
  SHA-256 of the token is stored); HttpOnly + SameSite cookies, Secure in production; logout and
  password change revoke sessions; uniform "invalid email or password" with constant work.
- **Authorization**: roles FOUNDER/ADMIN/STAFF/READ_ONLY enforced on every route by dependency;
  approvals decided only by Founder/Admin, Admins cannot approve their own requests; conversations
  are private to their owner.
- **CSRF**: token bound to the session (HMAC) required on every state-changing API call.
- **Injection**: SQLAlchemy ORM/parameterised queries only; CSV export and import neutralise
  spreadsheet formulas; HTML (landing pages, invoices, unsubscribe page) is escaped and served with
  restrictive CSPs; no inline scripts anywhere (`script-src 'self'`).
- **AI safety**: fixed application rules prepended to every agent prompt; untrusted data wrapped
  and escaped; tool permissions and delegation limits enforced in Python; agents can only draft
  and propose; external actions require a human approval and are never executed by the app.
- **Secrets**: only in `.env` (git-ignored); never returned by the API or sent to the browser;
  `.env.example` has placeholders; history scanned for keys before each push.
- **Logging/audit**: JSON logs with request IDs; audit log of logins (incl. failures), user and
  record changes, approvals, exports, payments.

## Phase 8: outbound web requests

Website research is the first feature that makes AgencyOS request external URLs, so it is treated
as an SSRF surface: only http/https on ports 80/443, no credentials in URLs, every hostname and
redirect target must resolve exclusively to public addresses (private, loopback, link-local,
multicast, reserved and cloud-metadata ranges are refused), `localhost`/`.local`/`.internal`-style
names are refused, responses are capped (1.5 MB, 10 s, HTML/text only), robots.txt is honoured and
the user agent identifies AgencyOS. Fetched text is reduced to facts and short excerpts and reaches
agents only as untrusted data; script contents are never extracted; contact details are not stored.
Residual risk: DNS rebinding between the check and the connection.

## Phase 9: two-factor authentication and sessions

- TOTP (RFC 6238: SHA-1, 6 digits, 30 s, ±1 step for clock drift). A used step is stored per user,
  so a code cannot be replayed, even inside its window.
- The secret is encrypted at rest (Fernet; key derived from `SECRET_KEY` with HKDF) and is shown
  only during setup. Setup is confirmed with a valid code before it takes effect.
- Ten single-use recovery codes, shown once, stored as SHA-256 hashes.
- Sign-in with 2FA: the password step returns no session, only a signed 5-minute token bound to
  the current password hash (a password change voids pending sign-ins). Wrong codes count against
  the same per-account limiter as wrong passwords (5 per 15 minutes).
- Turning 2FA off needs the password and a current code or recovery code; new recovery codes need
  a current code. Every change is audit-logged and other sessions are signed out on enable.
- `REQUIRE_2FA_FOR_ADMINS` blocks Founders/Admins without 2FA (403 `mfa_setup_required`) from
  everything but their own account settings.
- Users can list their sessions (device, IP, last active) and sign out any other one.
- The development-only demo login does not ask for a code; it is refused outside development.

## Phase 10: email sending

- Off unless `EMAIL_TRANSPORT=smtp`. Only messages created by a person's approval request and
  approved by a Founder/Admin can be sent, and only while the campaign is started (ACTIVE).
  Agents have no path to create, approve or start a send.
- Messages are rendered and frozen at approval-request time; editing an approved email cancels
  its approved copies. Emails with unfilled merge fields are never sent.
- Right before each send: do-not-contact list, deleted prospect, changed address, and (for
  follow-ups) the prospect's stage are re-checked. Hard SMTP rejections add the address to
  Do-not-contact as BOUNCED.
- Rate limits: `EMAIL_DAILY_LIMIT` per rolling 24 hours, `EMAIL_SEND_INTERVAL_SECONDS` between
  emails, `EMAIL_MAX_RECIPIENTS` per campaign. `EMAIL_REDIRECT_TO` for dry runs.
- SMTP password is read from `.env` only, never returned by the API or logged; TLS is verified
  (`starttls`/`ssl`); `SMTP_SECURITY=none` with a password raises a configuration warning.
- `List-Unsubscribe` + RFC 8058 one-click with a public URL; otherwise reply-to-unsubscribe.
- Tests use a fake transport and a patched `smtplib`; no live mailbox is used.
- Not handled: reading bounce/complaint emails or provider webhooks, open/click tracking (not
  collected), DKIM/SPF/DMARC (configure these at your provider and DNS).

## Known limitations / accepted risks

- Rate limiting and the background task runner are in-memory and per process (run one process).
- Two-factor authentication is optional unless `REQUIRE_2FA_FOR_ADMINS=true`; there are no
  hardware security keys (WebAuthn) yet.
- No automatic account lockout notification by email (email is used only for approved campaigns).
- SQLite files and backups are not encrypted at rest; use disk encryption and encrypted off-site
  storage for backups.
- Session tokens are bearer tokens; anyone with access to a signed-in browser has that session.
- LLM providers receive the data included in prompts (prospect/client fields, report figures).
  Review your provider's data-retention terms.

## Reporting

This is a private internal tool. Report suspected issues to the Founder directly; do not open
public GitHub issues containing sensitive details.
