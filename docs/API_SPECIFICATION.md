# AgencyOS — API Specification

Base path: `/api/v1`. Interactive OpenAPI docs: `http://localhost:8090/docs`.

## Conventions

- JSON in, JSON out. Timestamps ISO-8601 UTC. Money as decimal **strings** (`"1500.00"`) so no
  float rounding reaches the client.
- **Auth:** session cookie `agencyos_session` (HttpOnly) set by `POST /auth/login`.
- **CSRF:** every `POST/PUT/PATCH/DELETE` made with a session must send header
  `X-CSRF-Token: <value of the agencyos_csrf cookie>`.
- **Lists:** `?page=1&page_size=25` (max 100), plus resource-specific filters and `sort`.
  Response: `{"items": [...], "total": 42, "page": 1, "page_size": 25}`.
- **Errors:** always

  ```json
  {"error": {"code": "not_found", "message": "Prospect not found", "details": null},
   "request_id": "8f1c..."}
  ```

  | Status | code |
  |---|---|
  | 400 | `bad_request` |
  | 401 | `unauthenticated` |
  | 403 | `forbidden` / `csrf_failed` |
  | 404 | `not_found` |
  | 409 | `conflict` (duplicates, invalid state transition) |
  | 422 | `validation_error` (details lists field errors) |
  | 429 | `rate_limited` |
  | 500 | `internal_error` (no internals leaked) |

- **Roles:** READ_ONLY may only read. STAFF may create/edit business records. ADMIN additionally
  manages users and agents. FOUNDER can do everything, including approval decisions.

## Phase 1 endpoints (implemented)

### Health
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | Liveness: `{"status":"ok","version":..,"environment":..}` |
| GET | `/health/ready` | none | Readiness: checks DB; 503 if unavailable |

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/login` | none (rate-limited) | Body `{email, password}`. Sets cookies, returns `{user, csrf_token}` |
| POST | `/auth/logout` | session | Revokes session, clears cookies. 204 |
| GET | `/auth/me` | session | Current user |
| POST | `/auth/change-password` | session | `{current_password, new_password}`; revokes other sessions. 204 |
| GET | `/auth/demo-login/status` | none | `{"enabled": bool}` |
| POST | `/auth/demo-login` | none | Dev only; 404 unless demo login is enabled |
| GET | `/auth/users` | FOUNDER, ADMIN | List users |
| POST | `/auth/users` | FOUNDER, ADMIN | Create user `{email, full_name, password, role}`. Only FOUNDER can create FOUNDER/ADMIN |

### Dashboard
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/dashboard/summary` | any role | `{kpis, charts, activity, is_empty}` — all from DB aggregates |

### Settings
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/settings/system` | any role | Environment, version, DB backend, LLM provider name and whether keys are *configured* (never the keys) |

## Phase 2 endpoints (implemented)

Execution is asynchronous: endpoints that start work return **202** with a `task_id`; poll
`GET /tasks/{id}` until `status` is no longer `PENDING`/`ASSIGNED`/`RUNNING`. Writes need FOUNDER,
ADMIN or STAFF; agent edits and approval decisions need FOUNDER or ADMIN; reads need any role.

### AI CEO
| Method | Path | Description |
|---|---|---|
| POST | `/ceo/messages` | `{content, conversation_id?}` → 202 `{conversation_id, message_id, task_id}`. Creates the conversation when `conversation_id` is null |
| GET | `/ceo/conversations` | Caller's conversations (private per user), newest first |
| GET | `/ceo/conversations/{id}` | Messages; each user message carries its live task tree (`task.children[]`) |
| PATCH | `/ceo/conversations/{id}` | Rename `{title}` |
| DELETE | `/ceo/conversations/{id}` | Soft delete (204). Tasks and executions are kept |

### Agents
| Method | Path | Description |
|---|---|---|
| GET | `/agents` | All agents with stats (`executions`, `succeeded`, `failed`, `tokens`, `estimated_cost`, `open_tasks`) and form options |
| POST | `/agents` | Create a custom agent (201). Tools limited to `draft_content`, `read_business_data`, `propose_external_action` |
| GET | `/agents/{id}` | Detail incl. effective provider/model |
| PATCH | `/agents/{id}` | Edit prompt, model, temperature, active flag, etc. CEO cannot be deactivated; built-in tool permissions are fixed |
| POST | `/agents/{id}/reset-prompt` | Restore the built-in default prompt |
| POST | `/agents/{id}/test` | `{instructions}` → 202 `{task_id}` (task_type `agent_test`) |
| GET | `/agents/{id}/executions` | Paged execution history (tokens, cost, duration, errors) |

### Tasks
| Method | Path | Description |
|---|---|---|
| GET | `/tasks` | Filters: `status`, `agent_id`, `root_only`, `q`; paged |
| POST | `/tasks` | `{agent_id, title, instructions, priority, context?}` → 202. Assigning to the AI CEO plans and delegates |
| GET | `/tasks/{id}` | Task tree, input, output, executions, approvals. `phase` adds `PLANNING` for a CEO task that has not delegated yet |
| POST | `/tasks/{id}/cancel` | Cancels the task and unfinished sub-tasks; their pending approvals become `EXPIRED` |
| POST | `/tasks/{id}/retry` | `FAILED` → `PENDING` with a fresh bounded attempt budget (202) |

### Approvals
| Method | Path | Description |
|---|---|---|
| GET | `/approvals` | Filter `status` (PENDING/APPROVED/REJECTED/EXPIRED); overdue requests expire on read |
| POST | `/approvals/{id}/decision` | `{decision, comments?}` with decision `APPROVED` or `REJECTED`. Returns `executed: false`: no integration is connected, so nothing is sent. When no request on a task is pending, the task becomes `COMPLETED` (any approved) or `CANCELLED` |

## Phase 3 endpoints (implemented)

Writes need FOUNDER, ADMIN or STAFF; deleting clients and projects needs FOUNDER or ADMIN.

### Prospects
| Method | Path | Description |
|---|---|---|
| GET | `/prospects` | Filters `q`, `stage`, `industry`, `city`, `min_score`, `follow_up_due`; `sort` (company_name, lead_score, created_at, updated_at, next_follow_up_on, stage, city) + `order`; paged; returns `facets` (industries, cities) |
| POST | `/prospects` | Create. 409 with `details.duplicates[]` unless `allow_duplicate: true` |
| POST | `/prospects/check-duplicates` | Same matching without saving |
| GET/PATCH/DELETE | `/prospects/{id}` | Detail (with `activities[]`, `client_id`), update (stage changes logged), soft delete |
| POST | `/prospects/{id}/activities` | `{activity_type: note/call/email/meeting/follow_up, description, mark_contacted}` |
| POST | `/prospects/{id}/convert` | `{service_package, monthly_fee, setup_fee, start_date}` → client (201); stage becomes WON; 409 if already converted |
| POST | `/prospects/{id}/research` | Sales Agent task with this record as untrusted context → 202 `{task_id}` |
| POST | `/prospects/import` | Multipart `file` (.csv, ≤ 2 MB, ≤ 5,000 rows), `skip_duplicates` → `{created, skipped_duplicates[], errors[], ignored_columns[]}` |
| GET | `/prospects/export` | CSV with the same filters; formula-injection safe |

### Clients
| Method | Path | Description |
|---|---|---|
| GET | `/clients` | Filters `q`, `contract_status`; sort `company_name`/`created_at`/`monthly_fee`/`start_date`; includes `active_projects` |
| POST | `/clients` | Create (409 on duplicate company name) |
| GET | `/clients/{id}` | Detail with `projects[]`, `deliverables[]`, `invoices[]`, `payments[]`, `activity[]` |
| PATCH | `/clients/{id}` | Update; end date may not precede start date |
| DELETE | `/clients/{id}` | Soft delete; 409 while the client has active projects |

### Projects
| Method | Path | Description |
|---|---|---|
| GET | `/projects` | Filters `client_id`, `status`, `q` |
| GET | `/projects/options` | Active users, agents and clients for forms |
| POST | `/projects` | Create `{client_id, name, scope, dates, budget, status, agent_ids}` |
| GET | `/projects/{id}` | Detail with `tasks[]`, `deliverables[]`, `timeline[]`, `agent_tasks[]` |
| PATCH | `/projects/{id}` | Update. Changing an existing `scope` requires `scope_change_reason`; `completion_percent` is read-only once tasks exist |
| DELETE | `/projects/{id}` | Soft delete |
| POST/PATCH/DELETE | `/projects/{id}/tasks[/{task_id}]` | Tasks with `assignee_user_id`, `assignee_agent_id`, `depends_on_task_id` (same project, no cycles, blocks start/finish), `due_date` |
| POST | `/projects/{id}/tasks/{task_id}/run-agent` | Send the task to its assigned agent → 202 `{task_id}` (AI task linked by `project_id`) |
| POST | `/projects/{id}/comments` | `{body, project_task_id?}` |
| POST/PATCH | `/projects/{id}/deliverables[/{id}]` | Title, status (DRAFT → DELIVERED sets `delivered_at`), link, due date |

`GET /approvals/{id}` was added for single approvals.

## Phase 4 endpoints (implemented)

Nothing here sends email, launches ads or publishes pages. "Request approval" endpoints create an
`approvals` row with `payload.entity_type`/`entity_id`; the decision updates the record.

### Campaigns
| Method | Path | Description |
|---|---|---|
| GET/POST | `/campaigns` | List (filters `q`, `status`, `campaign_type`, `client_id`) / create a DRAFT |
| GET/PATCH/DELETE | `/campaigns/{id}` | Detail (emails, drafts, audience summary, readiness, approvals, AI tasks, allowed status changes). PATCH on APPROVED/ACTIVE/PAUSED accepts only `budget` and raises a CHANGE_BUDGET approval |
| PUT | `/campaigns/{id}/audience` | `{stages[], industry, city, min_score}` → segment counts (matched, with_email, suppressed, eligible) |
| POST | `/campaigns/{id}/generate` | `{kind: strategy/emails/ads/audience, notes}` → 202 draft-only agent task |
| POST | `/campaigns/{id}/import/{task_id}` | `{indexes?}` → email_draft deliverables become sequence emails; others saved as drafts |
| DELETE | `/campaigns/{id}/drafts/{index}` | Remove a saved draft |
| POST | `/campaigns/{id}/request-approval` | Email types need `consent_basis` + `confirm_compliance`; checks readiness (emails compliant, audience eligible, ad budget) |
| POST | `/campaigns/{id}/status` | Manual ACTIVE/PAUSED/COMPLETED/CANCELLED per allowed transitions |
| PUT | `/campaigns/{id}/metrics` | Manual metrics; returns derived rates and cost per lead |
| POST | `/campaigns/{id}/analyze` | Analytics Agent task over the metrics → 202 |

### Email
| Method | Path | Description |
|---|---|---|
| GET/POST | `/emails/templates` | List (filters `campaign_id`, `kind`) / create; each item includes `compliance[]` |
| GET/PATCH/DELETE | `/emails/templates/{id}` | Edits reset status to DRAFT; campaign emails follow the campaign's lock rules |
| GET | `/emails/templates/{id}/preview?prospect_id=` | Personalised rendering; `missing_fields[]`, signed unsubscribe link, suppression flag |
| POST | `/emails/check` | Compliance check for `{subject, body_text}` |
| POST | `/emails/generate` | `{purpose: sequence/subject_lines/single, brief, campaign_id?}` → 202 |
| GET | `/emails/ai-tasks` · POST `/emails/import/{task_id}` | Library AI drafts and import as templates |
| POST | `/emails/segments/preview` | Audience counts for a filter |
| POST | `/emails/recipients/check` | `{emails[]}` → eligible / suppressed / invalid / duplicates (nothing stored) |
| GET/POST | `/emails/suppressions` | List / add (`reason` MANUAL/UNSUBSCRIBED/BOUNCED/COMPLAINT) |
| DELETE | `/emails/suppressions/{id}` | FOUNDER/ADMIN; refused for UNSUBSCRIBED and COMPLAINT |
| GET | `/emails/settings` | Sender identity (non-secret) and `sending_enabled: false` |
| GET/POST | `/unsubscribe?e=&t=` (outside `/api`) | Public, HMAC-signed confirm page and POST that records an UNSUBSCRIBED suppression |

### Funnels
| Method | Path | Description |
|---|---|---|
| GET | `/funnels/templates` | hvac, plumbing, roofing, accounting, real_estate |
| GET/POST | `/funnels` | List / create (optionally `template_key`) |
| GET/PATCH/DELETE | `/funnels/{id}` | Detail with `validation[]`, `mobile_checks[]`, approvals, AI tasks. Editing an APPROVED/PUBLISHED funnel returns it to DRAFT |
| GET | `/funnels/{id}/preview` | Generated HTML with preview banner; CSP `default-src 'none'; form-action 'none'`, `X-Frame-Options: SAMEORIGIN` |
| GET | `/funnels/{id}/download` | HTML attachment for hosting |
| POST | `/funnels/{id}/request-approval` | PUBLISH_WEBSITE approval; requires no validation errors and all mobile checks passing |
| POST | `/funnels/{id}/published` | `{landing_page_url}` after hosting (APPROVED → PUBLISHED) |
| POST | `/funnels/{id}/generate` · `/funnels/{id}/import/{task_id}` | Funnel Agent copy (Headline, Subheadline, Benefits, Proof, Call to action) and import |

## Phase 5 endpoints (implemented)

Money is a decimal string with two places. Period parameters `start`/`end` are ISO dates.

### Finance
| Method | Path | Description |
|---|---|---|
| GET | `/finance/summary?start&end` | Revenue, expenses, gross profit, margin, MRR, outstanding, overdue, by-category totals, `monthly` 12-month series, `profitability` rows |
| GET/POST | `/finance/revenue` | List (filters) / add manual revenue (FOUNDER/ADMIN) |
| PATCH/DELETE | `/finance/revenue/{id}` | Manual entries only; payment-linked revenue is changed via its payment |
| GET/POST | `/finance/expenses` | List / add (STAFF+) |
| PATCH/DELETE | `/finance/expenses/{id}` | Update (STAFF+) / soft delete (FOUNDER/ADMIN) |
| GET | `/finance/ai-usage?month=YYYY-MM` | Estimated AI cost by provider/model from executions, and AI expenses already recorded |
| POST | `/finance/ai-usage/expense` | `{month, amount, vendor}` → AI_API expense |
| GET/POST | `/finance/invoices` | List (`status`, `client_id`, `overdue`) / create draft `{client_id, items[], issue_date, due_date, notes}` |
| POST | `/finance/invoices/from-client/{client_id}` | Draft from the client's monthly fee (and setup fee if requested) |
| GET/PATCH/DELETE | `/finance/invoices/{id}` | Detail with items and payments; edit/delete drafts only |
| POST | `/finance/invoices/{id}/mark-sent` | DRAFT → SENT (records that you sent it) |
| POST | `/finance/invoices/{id}/void` | FOUNDER/ADMIN; refused when payments exist |
| GET | `/finance/invoices/{id}/print` | Printable HTML (escaped, own CSP) |
| GET/POST | `/finance/payments` | List / record `{invoice_id? , client_id?, amount, paid_on, method, reference, category}`; creates revenue and updates invoice status |
| DELETE | `/finance/payments/{id}` | FOUNDER/ADMIN; removes its revenue and recalculates the invoice |

### Analytics
| Method | Path | Description |
|---|---|---|
| GET | `/analytics/reports` | Report types with default periods |
| GET | `/analytics/reports/{key}?start&end` | `{key, title, period, kpis[], sections[{title, columns, rows, note?}]}` |
| GET | `/analytics/reports/{key}/csv` | CSV attachment of the same report |
| POST | `/analytics/reports/{key}/summarize` | Draft-only Analytics Agent task over the report → 202 |
| GET | `/analytics/summaries` | Recent report-summary tasks |

Report keys: `daily_activity`, `weekly_ceo`, `monthly_business`, `prospect_performance`,
`sales_conversion`, `campaign_performance`, `project_progress`, `financial_summary`, `agent_performance`.

## Phase 6 changes

| Method | Path | Description |
|---|---|---|
| PATCH | `/auth/users/{id}` | `{is_active?, role?}` (FOUNDER/ADMIN). Deactivation and role changes revoke the user's sessions. Only a Founder manages Founder/Admin accounts; nobody changes themself; the last active Founder is protected |
| GET | `/settings/system` | Adds `warnings[]` (configuration issues) and `api_docs_enabled` |

Other behaviour: `POST /auth/login` returns 429 after 5 failed attempts for the same account within
15 minutes; request bodies over 5 MB return 413; chunked bodies without a length return 411;
database constraint conflicts return 409; `/docs` and `/openapi.json` are disabled in production.


## Phase 8 endpoints (implemented)

| Method | Path | Description |
|---|---|---|
| POST | `/prospects/{id}/website-check` | Check the prospect's public website now; returns the check (`status` DONE/BLOCKED/ERROR, `signals` incl. `conversion_flags[]` and `conversion_gap_score`, `pages[]`, `title`) |
| GET | `/prospects/{id}/website-checks` | Last 20 checks |
| POST | `/prospects/{id}/apply-website-findings` | Append the latest findings to observations; NEW → RESEARCHED |
| POST | `/prospects/website-checks` | `{prospect_ids?}` (else the list filters) → 202 `{queued, skipped[]}`; runs in the background (max 50) |
| POST | `/prospects/conversion-review` | `{prospect_ids?, limit}` → 202 `{task_id}`; Sales Agent ranks checked prospects by likely lead loss |

`GET /prospects` items and `GET /prospects/{id}` now include `website_check` (summary / latest check).
`POST /prospects/{id}/research` includes the latest website facts in the agent's context.

## Phase 9 endpoints (implemented)

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | Now returns `{mfa_required: true, mfa_token}` (no session) when the user has 2FA, else `{mfa_required: false, user, csrf_token}` |
| POST | `/auth/login/2fa` | `{mfa_token, code?, recovery_code?}` → session (`{user, csrf_token}`); 401 wrong/expired, 429 throttled |
| GET | `/auth/2fa` | `{enabled, enabled_at, recovery_codes_left, required}` |
| POST | `/auth/2fa/setup` | Start enrolment → `{secret, otpauth_uri, qr_svg}` (not active until enabled) |
| POST | `/auth/2fa/enable` | `{code}` → `{recovery_codes[10]}` (shown once); signs out other sessions |
| POST | `/auth/2fa/disable` | `{password, code? \| recovery_code?}` → 204 (refused when the policy requires 2FA) |
| POST | `/auth/2fa/recovery-codes` | `{code}` → new `{recovery_codes}`; old ones stop working |
| GET | `/auth/sessions` | `{items[]: id, created_at, last_seen_at, ip_address, user_agent, current}` |
| DELETE | `/auth/sessions/{id}` | Sign out another session (400 for the current one; use logout) |
| POST | `/auth/sessions/revoke-others` | → `{revoked}` |

With `REQUIRE_2FA_FOR_ADMINS=true`, a Founder/Admin without 2FA gets 403 `mfa_setup_required` on
every route except `/auth/me`, `/auth/logout`, `/auth/2fa*`, `/auth/change-password`,
`/auth/sessions*` and `/settings/system`. `UserOut` includes `totp_enabled`.

## Phase 10 endpoints (implemented)

| Method | Path | Description |
|---|---|---|
| POST | `/campaigns/{id}/request-approval` | Email campaigns now also render and freeze every message (`email_messages`); details state emails, recipients, steps and exclusions |
| GET | `/campaigns/{id}/outbox` | `?status=&page=&page_size=` → `{items[]: id, company_name, to_email, step, delay_days, subject, body_text, status, send_after, sent_at, attempts, error; counts}` |
| POST | `/campaigns/{id}/status` | `ACTIVE` starts sending approved emails; `PAUSED` holds them; `CANCELLED`/`COMPLETED` cancel unsent ones |
| GET | `/emails/sending` | `{transport, enabled, configured, smtp_host, smtp_port, smtp_security, from, from_name, daily_limit, sent_last_24h, scheduled, interval_seconds, redirect_to, unsubscribe_mode (link\|reply), last_run_at, last_sent_at, last_error}` (no secrets) |
| POST | `/emails/sending/test` | Send a test email to the signed-in user's own address → `{sent_to, message_id}`; 400 when sending is off |
| POST | `/emails/templates/{id}/test-send` | `{prospect_id?}` → the email personalised for that prospect, sent only to the signed-in user, subject `[Test] …` |
| POST | `/unsubscribe?e=&t=` | Also accepts RFC 8058 one-click POSTs (e/t in the query string) |

Campaign detail includes `outbox` (counts by status) for email campaigns. Message statuses:
`PENDING_APPROVAL → SCHEDULED → SENDING → SENT | FAILED`, or `SKIPPED` / `CANCELLED`.
`/settings/system` `external_sending_enabled` is true when SMTP sending is configured.
