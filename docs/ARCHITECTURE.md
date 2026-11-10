# AgencyOS — Architecture

## Shape

A **modular monolith**: one FastAPI process serves both the REST API (`/api/v1/...`) and the
static frontend (`/`). One database (SQLite now, PostgreSQL later via the same SQLAlchemy models).

```
Browser (vanilla JS + Chart.js)
   │  fetch, HttpOnly session cookie + X-CSRF-Token header
   ▼
FastAPI app (backend/app/main.py)
   ├─ middleware: request-id → security headers → rate limit → CSRF
   ├─ api/v1/*            thin route handlers (validation, auth deps, status codes)
   ├─ services/*          business logic (no FastAPI imports)
   ├─ security/*          passwords, sessions, CSRF, role checks
   ├─ orchestrator/*      (Phase 2) task state machine, delegation, retries, timeouts
   ├─ agents/*            (Phase 2) BaseAgent + specialised agents
   ├─ providers/*         (Phase 2) LLM providers: OpenAI, Anthropic, Mock
   ├─ approvals/*         approval gate: record-level requests + per-entity decision handlers
   ├─ marketing/*         email compliance, funnel templates, landing-page renderer/validator
   ├─ integrations/*      (Phase 4+) email/CRM/calendar/ads interfaces + mock implementations
   ├─ models/*            SQLAlchemy ORM
   └─ database/*          engine, session factory, base mixins
   ▼
SQLite file: agencyos/data/agencyos.db   (Alembic-managed)
```

### Layering rules

- Route handlers do request parsing, auth, and HTTP mapping only. They call services.
- Services take a SQLAlchemy `Session` as an argument (dependency injection) and raise
  domain errors from `app.utils.errors`; the central handler maps them to HTTP responses.
- Agents never touch the database or the network directly. They receive validated input and
  return validated output; the orchestrator persists everything and enforces permissions.
- Only `providers/` and `integrations/` import vendor SDKs.

## Security model

| Concern | Implementation |
|---|---|
| Passwords | `hashlib.scrypt` (n=2¹⁴, r=8, p=1, 16-byte salt), constant-time compare; no plaintext ever stored or logged |
| Sessions | 32-byte random token in an HttpOnly, SameSite=Lax cookie (`Secure` in production). Only the SHA-256 hash is stored in `user_sessions`, so a DB leak does not leak live sessions. Logout revokes the row. Idle expiry configurable |
| CSRF | Unsafe methods under `/api/` that carry a session cookie must send `X-CSRF-Token` = HMAC-SHA256(secret, session token). The value is also exposed in a readable `agencyos_csrf` cookie for the frontend |
| Authorization | Roles FOUNDER > ADMIN > STAFF > READ_ONLY. `require_roles()` dependency on every protected route. READ_ONLY is blocked from all writes |
| Rate limiting | Sliding window per client IP (login 10/min, API 300/min) and per account (5 failed logins / 15 min). In-memory (single process). Behind a proxy, client IPs come from `X-Forwarded-For` only when sent by `FORWARDED_ALLOW_IPS` |
| Headers | CSP `default-src 'self'` (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS when Secure cookies are on. Request bodies over 5 MB rejected before parsing |
| CORS | Disabled by default (same-origin app). Allowed origins configurable via `CORS_ORIGINS` |
| Errors | Uniform JSON `{error:{code,message,details}, request_id}`; stack traces only in logs |
| Secrets | `.env` only; `.env.example` holds placeholders. `SECRET_KEY` must be set (≥32 chars) when `APP_ENV=production` |
| Demo login | Requires `APP_ENV=development` **and** `ENABLE_DEMO_LOGIN=true`; settings refuse to load if the flag is on in production |
| Audit | `audit_logs` row for logins, logouts, failed logins, user creation, password changes, and (later) every state change and approval decision |

## Agent design (implemented in Phase 2)

```
BaseAgent
  key, name, role, department, system_prompt, capabilities, allowed_tools,
  input_model (Pydantic), output_model (Pydantic), provider, model, temperature
  execute(task_input) -> AgentResult
    validate_input → build messages (system prompt + untrusted data block)
    → provider.generate(json schema) → validate_output → record_execution
    errors → handle_error → FAILED with message, bounded retries
```

- Agent configuration (prompt, model, temperature, active flag) lives in the `agents` table and is
  editable in the UI; the Python class supplies schemas and allowed tools.
- The CEO agent produces a **plan** (list of sub-tasks with target agent keys). The orchestrator,
  not the LLM, validates the plan: agent must exist and be active, depth ≤ 2, no agent may appear
  twice in one delegation chain (prevents cycles), and any action tagged external becomes an
  approval request.
- Task state machine: `PENDING → ASSIGNED → RUNNING → (COMPLETED | FAILED | AWAITING_APPROVAL)`,
  `AWAITING_APPROVAL → (COMPLETED | CANCELLED)`, any non-terminal → `CANCELLED`,
  `FAILED → PENDING` (retry, bounded). Transitions are checked in one function.
- **Execution (Phase 7).** The database is the queue: a task waiting to run is `PENDING`. A
  `QueueWorker` claims tasks with a conditional `UPDATE … WHERE status='PENDING'` (so two workers,
  in threads or separate processes, can never take the same task), most urgent first, and runs up to
  `TASK_WORKERS` requests at once. It runs inside the web process by default or alone via
  `python -m app.cli worker`. API calls only `runner.submit(task_id)` (which just wakes the worker);
  tests use `InlineRunner`. On start-up, running sub-tasks are failed and running top-level
  requests are re-queued once (`recover_interrupted`); queued tasks are left to run.
- **Parallel specialists.** The CEO's accepted sub-tasks run concurrently in a thread pool
  (`ORCHESTRATOR_PARALLEL_AGENTS`), each with its own database session; one failing sub-task does
  not affect its siblings. SQLite waits up to 30 s for write locks (WAL mode).
- **Budgets.** `orchestrator/budget.py` checks the request's estimated spend (root + sub-tasks) and
  the month's spend before every model call, including the CEO summary; a breach fails that call
  with a clear, non-retryable error. The monthly cap also refuses new requests at creation (409).
- Flow for a CEO request: root task (CEO) → plan call → plan validated in Python → child tasks
  (depth 1) run in parallel → CEO summary call → assistant message posted to the
  conversation. If the summary call fails, a plain summary is built in Python and labelled as such.
- Specialists with `read_business_data` receive a read-only KPI snapshot from the database in their
  untrusted data block, so financial and status answers use real figures.
- Tool permissions: `delegate` (CEO only), `draft_content`, `read_business_data`,
  `propose_external_action`. Proposals from an agent without the last one are dropped and noted.
- Approvals: one `approvals` row per proposed action, expires after 7 days. Approving records the
  decision only; Phase 4+ integrations will execute approved actions.

### Providers

| Provider | Structured output | Notes |
|---|---|---|
| `mock` | Deterministic builders per schema | Default. Labels output `[Mock]`. No cost |
| `openai` | `response_format: json_schema` (strict) | Sends temperature |
| `anthropic` | `output_config.format` (JSON schema) + `effort` | Default model `claude-opus-5-5`. No temperature (current Claude models reject sampling params), thinking left at the model default. Server-side refusal fallback (`fallbacks: "default"`) on current models; a refusal that survives it fails the task |

Schemas are converted once for both vendors (`strict_json_schema`: inline `$ref`, every field
required, `additionalProperties: false`); value limits are enforced afterwards by Pydantic.
Costs use list prices in `app/providers/pricing.py`; unknown models report no cost rather than a guess.
- `MockLLMProvider` returns deterministic, schema-valid output so the whole system runs offline.

## Marketing safety model (Phase 4)

- **Draft-only agent work.** Tasks of type `campaign_generate`, `campaign_analyze`, `email_generate`
  and `funnel_generate` never create approvals from agent proposals; approval is requested for the
  whole record by a person, with the full context (audience size, consent basis, budget).
- **Record-level approvals.** `approvals.payload.entity_type/entity_id` link an approval to a campaign
  or funnel; `app.approvals.settle()` calls the handler the owning service registered. Content is
  locked while awaiting approval, and edits after approval return the record to draft.
- **External execution is limited to approved email.** For ads and funnels, "Active" and
  "Published" are set by a person after acting in an external tool. Email campaigns are the one
  exception (Phase 10): `email_messages` holds every rendered email, frozen when approval is
  requested, so exactly what the approver read is what gets sent. A background sender
  (`app/services/email_sending.py`) sends due messages of ACTIVE campaigns over SMTP
  (`app/integrations/email_transport.py`), claiming each row atomically and re-checking
  suppression and the prospect's stage first. Agents cannot create or approve sends.
- **Landing-page preview** is served with its own CSP (`default-src 'none'`, inline styles only,
  `form-action 'none'`) inside a sandboxed iframe; the middleware keeps route-set security headers.
- **Unsubscribe** links carry `HMAC(SECRET_KEY, email)`, so the public page needs no login and
  cannot be used to suppress arbitrary addresses.

## Configuration

All configuration is in `app/config/settings.py` (pydantic-settings) and read from environment or
`agencyos/.env`. See `.env.example` for every key.

## Logging

JSON lines to stdout: `ts, level, logger, message, request_id, path, method, status, duration_ms`.
Each response carries `X-Request-ID`.

## Deployment

- Native: `uvicorn app.main:app` from `backend/` (see README).
- Docker: single image, `docker compose up`; SQLite volume at `/app/data`. For multi-user hosting
  switch `DATABASE_URL` to PostgreSQL and replace the in-memory rate limiter.
