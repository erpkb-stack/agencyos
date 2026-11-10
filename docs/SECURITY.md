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

## Known limitations / accepted risks

- Rate limiting and the background task runner are in-memory and per process (run one process).
- No multi-factor authentication. Recommended next step if more people get access.
- No automatic account lockout notification by email (the app does not send email).
- SQLite files and backups are not encrypted at rest; use disk encryption and encrypted off-site
  storage for backups.
- Session tokens are bearer tokens; anyone with access to a signed-in browser has that session.
- LLM providers receive the data included in prompts (prospect/client fields, report figures).
  Review your provider's data-retention terms.

## Reporting

This is a private internal tool. Report suspected issues to the Founder directly; do not open
public GitHub issues containing sensitive details.
