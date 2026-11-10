# AgencyOS — Implementation Plan

## Repository inspection (2026-11-09)

The working folder `Job_Search_Claude/` already holds several unrelated projects
(`agency-website/`, `object-detection-app/`, `weather_agent/`, `spring-boot-interview-demo/`,
job-search data). None of them contain AgencyOS code. Reusable lessons:

- `object-detection-app/` uses the same FastAPI + vanilla-JS + pytest pattern, which confirms the
  stack works on this Mac (Python 3.12.4 from Anaconda).
- `agency-website/` is the public marketing site for the agency. It stays separate: AgencyOS is an
  internal tool and must not be coupled to the public site.

AgencyOS lives in its own folder, `agencyos/`, so it can become its own git repository. No existing
files outside `agencyos/` are modified.

Environment facts that shape the plan:

| Fact | Consequence |
|---|---|
| Python 3.12.4 available | Target 3.12+, use `.venv` inside `agencyos/` |
| Docker is **not installed** on this Mac | Dockerfile/compose are written but cannot be run locally until Docker Desktop is installed |
| Not a git repository | Recommend `git init` inside `agencyos/` |
| A `.env` exists in the parent folder | AgencyOS reads only `agencyos/.env`, never the parent one |

## Guiding decisions

1. **Modular monolith.** One FastAPI process, one database, clear internal module boundaries.
   No microservices, no message broker until load demands it.
2. **Deterministic core, LLM at the edges.** Permissions, task state machine, approvals and money
   math are plain Python. LLMs only produce content and plans, and their output is validated
   against Pydantic schemas before anything is stored.
3. **Integrations behind interfaces.** LLM providers, email, CRM, calendar and ads are protocols
   with a mock/demo implementation. Business logic never imports a vendor SDK directly.
4. **Nothing external without approval.** v1 sends no emails and publishes nothing. Any
   "external" action becomes an `approvals` row and stays a draft.
5. **No fake data.** The dashboard shows real database aggregates or an empty state.

## Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1 — Foundation** ✅ | Config, logging, DB + Alembic, full schema, auth (sessions, roles, CSRF, rate limit), health checks, error handling, app shell, dashboard, settings | Tests green; app runs at :8000; login + dashboard verified in browser |
| **2 — AI CEO** ✅ | Provider abstraction (OpenAI, Anthropic, Mock), BaseAgent + 11 agents, registry, orchestrator + state machine, conversations, CEO chat UI, agent management UI | CEO delegates one request to 3 agents under the mock provider; executions persisted |
| **3 — Business management** ✅ | Prospects (CRUD, search, CSV, dedupe, timeline), clients, projects, project tasks, approvals UI | CRUD + approval tests green; every screen wired to API |
| 4 — Marketing | Campaigns, email templates/sequences, suppression list, funnels, landing-page generation + preview | Drafts only; approval required for any send/publish |
| 5 — Finance & analytics | Revenue, expenses, invoices, payments, reports, CSV export, agent performance | Money math tested with Decimal |
| 6 — Production readiness | Security review, coverage, Docker, deployment docs | Full suite green |

### Phase 1 task list

- [x] Project skeleton, `requirements.txt`, `.env.example`, `.gitignore`
- [x] Settings via `pydantic-settings`, with production guards
- [x] Structured JSON logging + request-ID middleware
- [x] SQLAlchemy 2 models for **all** tables in `DATABASE_DESIGN.md` (single initial migration,
      so later phases add behaviour rather than reshaping the schema)
- [x] Alembic configured (batch mode for SQLite) + initial migration
- [x] Auth: scrypt password hashing, server-side sessions in HttpOnly cookie, CSRF double-submit
      bound to the session, login rate limiting, roles FOUNDER/ADMIN/STAFF/READ_ONLY
- [x] Dev-only demo login (refused unless `APP_ENV=development` **and** `ENABLE_DEMO_LOGIN=true`;
      settings validation fails at start-up if enabled in production)
- [x] CLI: `create-user`, `init-db`
- [x] Health (`/api/v1/health`, `/api/v1/health/ready`), centralized error responses
- [x] Dashboard API: KPIs, charts and activity feed from real aggregates
- [x] Frontend: login page, app shell (sidebar + top bar), dashboard with Chart.js, settings
      (profile, change password, system status), roadmap pages for unbuilt modules
- [x] Tests: auth, roles, CSRF, rate limit, dashboard aggregates, migrations, error format
- [x] Dockerfile + docker-compose (unverified locally, Docker missing)

### Phase 2 task list

- [x] Provider interface (`LLMRequest`/`LLMResponse`/`ProviderError`), strict JSON-schema output
- [x] `MockLLMProvider` (deterministic, schema-valid, never invents contact data), `OpenAIProvider`
      and `AnthropicProvider` on the official SDKs; token usage + estimated cost per call
- [x] `BaseAgent` (validate_input → generate_response → validate_output → record_execution →
      handle_error), `CEOAgent`, 10 specialist classes, Founder-created custom agents
- [x] Fixed application rules prepended to every editable prompt; untrusted data wrapped and escaped
- [x] Agent registry seeded on start-up / `init-db` without overwriting edits
- [x] Orchestrator: explicit state machine, plan validation (active agents only, depth ≤ 2,
      no cycles, no duplicates, ≤ 5 sub-tasks), bounded retries, cancellation, restart recovery
- [x] Tool permissions enforced in Python; proposed external actions become approval requests
- [x] Persistent multi-conversation CEO chat with history sent back to the model
- [x] Approvals list + decisions (records the decision; nothing is executed)
- [x] UI: AI CEO chat with live delegation tree, Agents (list, detail, edit, create, test,
      history), Tasks (filters, assign, detail, cancel, retry), Approvals
- [x] Tests: delegation to 3 agents, sales report from supplied data, marketing/email drafts,
      approval gate, permissions, retries, invalid output, persistence across restart, providers
      against fake SDK clients
- [ ] Not verified: real OpenAI / Anthropic calls (no keys on this machine; adapters are tested
      against fake clients only)

### Phase 3 task list

- [x] Prospects: CRUD with soft delete, validation (email, phone, website), search/filter/sort/paging
- [x] Duplicate detection (domain, email, phone, company + city) with explicit override
- [x] CSV import (size/row limits, header aliases, per-row errors, duplicate skipping) and export
      (formula-injection safe)
- [x] Activity timeline (manual notes/calls, stage changes, agent tasks); convert prospect → client
- [x] Clients: CRUD, contract status, fee validation, 360° view (projects, deliverables,
      invoices/payments, activity); deletion blocked while projects are active
- [x] Projects: status workflow, assigned agents, tasks (assignee person/agent, due dates,
      dependencies with cycle and blocking checks), comments, deliverables, timeline,
      progress from tasks, explicit scope-change reason
- [x] Agents use the records: "Ask Sales Agent" on a prospect, "Send to agent" on a project task,
      open projects in the business snapshot for Client Success / Finance / Analytics
- [x] UI: Prospects, Clients, Projects pages; scrollable dialogs; shared form builder
- [x] Tests: prospect CRUD/search/duplicates/CSV/conversion, client CRUD/rules, project tasks,
      dependencies, scope rule, deliverables, agent hand-offs, roles

## Technical risks

| Risk | Mitigation |
|---|---|
| SQLite has no native DECIMAL; values are stored as REAL | Columns are `Numeric(12,2)`; all money is quantized to cents in Python; the Postgres migration path gets real `NUMERIC` |
| In-memory rate limiter is per-process | Acceptable for a single local process; documented; swap for Redis when running multiple workers |
| LLM output is non-deterministic and may be malformed | Phase 2: JSON schema validation, bounded retries, mark task FAILED rather than guess |
| Prompt injection from prospect websites/emails | Phase 2: untrusted content is passed as quoted data, never merged into system prompts; tool permissions enforced in Python |
| Accidental real email/ad spend | No send/publish code paths exist in v1; approval rows only |
| Docker unavailable locally | Native Python path is the primary run mode; Docker files kept simple |
| Scope creep across 13 modules | Strict phase gates; a phase ends only with green tests |
