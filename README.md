# AgencyOS — AI Agency Operating System

Internal platform for running an AI-assisted marketing and automation agency: Founder dashboard,
AI CEO with specialised agents, CRM, projects, campaigns, funnels, finance and approvals.

**Status: all six phases complete** (Foundation, AI CEO, Business management, Marketing, Finance & analytics,
Production readiness). [![CI](https://github.com/erpkb-stack/agencyos/actions/workflows/ci.yml/badge.svg)](https://github.com/erpkb-stack/agencyos/actions/workflows/ci.yml) See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the phase roadmap.

| Works now | Coming next |
|---|---|
| Login/logout, roles, sessions, CSRF, rate limiting (per IP and per account), audit log | |
| Founder dashboard: 11 KPIs, 5 charts, activity feeds — all from the database | |
| **AI CEO chat**: persistent conversations; the CEO plans and delegates to specialists, live status per agent | |
| **11 agents** (CEO + 10 specialists) with editable prompts/models, custom agents, test runs, execution history | |
| **Prospects**: pipeline with search/filter/sort, duplicate detection, CSV import/export, activity timeline, convert to client, "Ask Sales Agent" | |
| **Clients**: contract status, fees, projects, deliverables, invoices/payments view, activity | |
| **Projects**: status workflow, tasks with assignees/due dates/dependencies, comments, deliverables, timeline, progress from tasks, "Send to agent" | |
| **Campaigns**: email and ad campaigns, AI drafts (strategy, sequences, ad copy), audience from prospects, approval gate, budget-change approvals, manual metrics + AI analysis | |
| **Email marketing**: template library, compliance checks, personalised preview, suppression list, signed unsubscribe links, recipient-list check | |
| **Funnels**: 5 industry templates, form builder, landing-page HTML with sandboxed desktop/mobile preview, form + mobile validation, publish approval, HTML download | |
| **Finance**: invoices (from client fees, printable), payments (drive paid status), revenue, expenses, AI-usage cost, profit, MRR, outstanding/overdue, client profitability estimate, 12-month chart | |
| **Analytics**: 9 reports (daily activity, weekly CEO, monthly business, prospects, sales conversion, campaigns, project progress, finance, agents) with CSV export and AI summaries | |
| **Task orchestration**: state machine, retries, cancellation, delegation limits, token + cost tracking | |
| **Approvals**: every external action an agent proposes waits for a Founder/Admin decision | |
| Providers: offline Mock (default), OpenAI, Anthropic | |

Every sidebar module is built. There are no placeholder buttons.

## Using the AI CEO

Open **AI CEO**, type a request (or pick an example) and press Enter. The CEO classifies it,
picks agents, and each agent's status updates live under your message: *Planning → Assigned →
Running → Completed / Failed / Awaiting approval*. The CEO then posts a summary. Open **Tasks** to
read every deliverable, see each provider call (tokens, estimated cost, duration) and cancel or
retry. Anything external (sending email, publishing, launching ads, spending) appears in
**Approvals**. Approving records your decision; no integration is connected yet, so **nothing is
sent or published**.

With the default `LLM_PROVIDER=mock` everything runs offline and answers are templated examples
(marked `[Mock]`). To use a real model, set a key in `.env` and restart:

```bash
LLM_PROVIDER=anthropic          # or openai
ANTHROPIC_API_KEY=sk-ant-...    # or OPENAI_API_KEY=sk-...
```

Individual agents can override the provider and model on their **Agents** page. Keys never leave
the server; the UI only shows whether one is configured.

## Requirements

- macOS with Python **3.12+** (`python3 --version`)
- Optional: Docker Desktop (for the container path)

## Quick start (native Python)

```bash
cd agencyos
./scripts/setup.sh
```

`setup.sh` creates `.venv`, installs dependencies, copies `.env.example` to `.env` with a random
`SECRET_KEY`, and creates the SQLite database at `data/agencyos.db`. If your default `python3` is
older than 3.12, run it as `PYTHON=python3.12 ./scripts/setup.sh`.

Create your Founder login (you will be prompted for a password: 10+ characters, mixed case, a digit):

```bash
./scripts/create_founder.sh you@youragency.com "Your Name"
```

Start the app:

```bash
./scripts/run_dev.sh
```

- App: http://localhost:8000
- API docs (OpenAPI): http://localhost:8000/docs

### Step by step (what the scripts do)

```bash
cd agencyos
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # then set SECRET_KEY
cd backend
../.venv/bin/python -m app.cli init-db
../.venv/bin/python -m app.cli create-user --email you@youragency.com --name "Your Name" --role FOUNDER
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

### Demo login (development only)

Set `ENABLE_DEMO_LOGIN=true` in `.env` to show a "Continue as demo founder" button on the login
page. It works only when `APP_ENV=development`; with `APP_ENV=production` the app refuses to start
if the flag is on.

## Tests

```bash
./scripts/test.sh                       # all tests
./scripts/test.sh --cov=app             # with coverage
```

Tests use a fresh temporary SQLite database for each test and never call external services.
Set `TEST_DATABASE_URL=postgresql+psycopg://…` to run the same suite on PostgreSQL (the database is
wiped, so use an empty one). CI runs SQLite with a 90% coverage gate, PostgreSQL, Bandit,
pip-audit and a Docker build + smoke test on every pull request.

## Docker and production

```bash
cp .env.example .env    # set APP_ENV=production, SECRET_KEY, PUBLIC_BASE_URL, provider key
docker compose up -d --build
docker compose exec agencyos python -m app.cli create-user --email you@youragency.com --name "Your Name" --role FOUNDER
```

The image runs in production mode by default, applies migrations on start, listens on
`127.0.0.1:8000`, and keeps data in the `agencyos-data` volume. Put an HTTPS reverse proxy in front.
Full guide (checklist, Caddy/nginx, PostgreSQL, backups, upgrades, monitoring):
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**. Security review: **[docs/SECURITY.md](docs/SECURITY.md)**.

> Docker isn't installed on the Mac this was built on; the image is built and smoke-tested by CI
> on every pull request (see the CI badge).

## Command line

Run from `backend/` with `../.venv/bin/python -m app.cli …` (or `docker compose exec agencyos python -m app.cli …`):

| Command | What it does |
|---|---|
| `init-db` | Apply migrations and add missing built-in agents |
| `create-user --email … --name … [--role FOUNDER]` | Create a login (prompts for the password) |
| `reset-password --email …` | Set a new password; signs the user out everywhere |
| `set-active --email … --active/--inactive` | Reactivate / deactivate a user |
| `backup-db [--dest DIR] [--keep 14]` | Consistent SQLite backup with integrity check |
| `check-config` | Show configuration and production warnings |

## Business records (Phase 3)

- **Prospects** — add manually or import a UTF-8 CSV (max 2 MB / 5,000 rows; a company-name column
  is required; common headers like *Company*, *Business Email*, *ZIP* are recognised). Duplicates are
  detected by website domain, email, phone, or company name in the same city; the form shows the
  match and lets you save anyway. Exports neutralise spreadsheet formulas. **Ask Sales Agent** sends
  only that record to the Sales Agent as untrusted data. **Convert to client** creates an active
  client and marks the prospect Won.
- **Clients** — clients with active projects cannot be deleted (Founder/Admin only, soft delete).
- **Projects** — once a project has tasks, progress is *done ÷ total*. A task can't start or finish
  before the task it depends on is done; cycles are refused. Scope changes require a reason and
  are written to the timeline. **Send to agent** hands a task to its assigned agent with the
  project as context; agents still only draft and propose.

## Marketing (Phase 4): drafts only

AgencyOS **never sends email, launches ads or hosts pages**. It drafts, checks and gets approval;
you run the approved work in your own tools and record the outcome.

- **Campaigns** — create, let agents draft (strategy, email sequence, ad copy, audience ideas),
  import what you want, pick an audience from your prospects (suppressed addresses are always
  excluded), then **Request approval**. Email campaigns require a consent basis and a compliance
  confirmation. After approval you can mark the campaign as running once you've launched it
  elsewhere, pause/complete it, enter metrics (rates and cost per lead are calculated) and ask the
  Analytics Agent for an analysis. A budget change on an approved campaign needs its own approval;
  editing an approved campaign's emails sends it back to draft.
- **Email rules** — every email must identify the sender (`{{sender_name}}`/`{{agency_name}}`),
  include the postal address (`{{agency_address}}`) and an unsubscribe (`{{unsubscribe_link}}`).
  Set `AGENCY_NAME`, `AGENCY_ADDRESS`, `SENDER_NAME`, `SENDER_EMAIL` in `.env`; until then previews
  show `[missing: …]`. Unsubscribe links point to `PUBLIC_BASE_URL/unsubscribe` and are signed, so
  they work without a login; unsubscribes and complaints can never be removed from the suppression list.
- **Funnels** — start from HVAC, plumbing, roofing, accounting or real-estate templates (generic copy,
  bracketed placeholders, no invented reviews). The page must pass form/content checks (contact
  field, labels, consent text, no placeholders left) and mobile checks before approval. After
  approval, **Download HTML**, host it, connect the form to your form handler/CRM, and **Mark as
  published** with the live URL. Any later edit needs approval again.

## Finance and analytics (Phase 5)

- **Cash basis.** Revenue is money received. Recording a payment creates its revenue entry;
  deleting the payment removes it. Manual revenue (no invoice) is Founder/Admin only.
- **Invoices** start as drafts (from line items or from a client's agreed monthly/setup fee),
  are numbered `INV-YYYY-NNNN`, and can be printed to PDF from the browser. "Mark as sent" only
  records that *you* sent it. **Paid / partially paid comes only from recorded payments**;
  overpayments and future-dated payments are refused; invoices with payments can't be voided.
- **Numbers**: total revenue, expenses, gross profit and margin, MRR (active clients' monthly
  fees), outstanding and overdue balances, and a client profitability **estimate** (revenue −
  AI cost of the client's project work − shared expenses allocated by revenue share).
- **AI usage**: estimated monthly AI cost from recorded agent calls (list prices); record the
  billed amount as an AI_API expense.
- **Reports** are built only from AgencyOS records, cover a chosen period (days follow the
  server's time zone), export to CSV (spreadsheet-formula safe) and can be summarised by the
  Analytics Agent, which only reads the report. PDF export of reports is not implemented yet.

## Configuration

All settings are environment variables (or `.env`). See [.env.example](.env.example). Key ones:

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `production` enforces a strong `SECRET_KEY`, secure cookies, no demo login, no debug |
| `SECRET_KEY` | random per start in dev | Signs CSRF tokens. Required (≥32 chars) in production |
| `DATABASE_URL` | `sqlite:///…/data/agencyos.db` | PostgreSQL: `postgresql+psycopg://…` (install `psycopg[binary]`) |
| `LLM_PROVIDER` | `mock` | `mock` / `openai` / `anthropic`; keys stay server-side |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | `claude-opus-5-5` / `gpt-4o-mini` | Default model per provider; agents can override |
| `ANTHROPIC_EFFORT` | `medium` | `low`–`max`; Claude's reasoning depth (cost vs. quality) |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `120` / `2` | Per provider call; the orchestrator adds its own bounded retry (2 attempts per task) |
| `ORCHESTRATOR_MAX_SUBTASKS` / `ORCHESTRATOR_MAX_DEPTH` | `5` / `2` | Delegation limits enforced in Python |
| `AGENCY_NAME`, `AGENCY_ADDRESS`, `SENDER_NAME`, `SENDER_EMAIL` | empty | Sender identity filled into email drafts |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base for unsubscribe links (use https in production) |
| `ENABLE_API_DOCS` | on except production | `/docs` and `/openapi.json` |
| `MAX_REQUEST_BYTES` | `5242880` | Larger request bodies get 413 |
| `LOGIN_ACCOUNT_LIMIT` / `LOGIN_ACCOUNT_WINDOW_SECONDS` | `5` / `900` | Failed logins per account before throttling |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Docker: reverse-proxy address trusted for client IPs |
| `ENABLE_DEMO_LOGIN` | `false` | Development only |

## Project layout

```
agencyos/
├── backend/
│   ├── app/
│   │   ├── api/v1/        route handlers (auth, dashboard, health, settings, ceo, agents, tasks, approvals,
│   │   │                  prospects, clients, projects)
│   │   ├── agents/        BaseAgent, CEOAgent + 10 specialists, registry, Pydantic I/O schemas
│   │   ├── approvals/     approval gate used by campaigns and funnels
│   │   ├── marketing/     email compliance, funnel templates, landing-page renderer/validator
│   │   ├── orchestrator/  state machine, delegation policy, engine, runners
│   │   ├── providers/     mock, OpenAI, Anthropic adapters; pricing
│   │   ├── config/        settings (pydantic-settings)
│   │   ├── database/      engine, session, base + mixins
│   │   ├── models/        SQLAlchemy models for all 26 tables
│   │   ├── schemas/       Pydantic request/response models
│   │   ├── security/      passwords, tokens, rate limiter, auth dependencies
│   │   ├── services/      business logic (auth, audit, dashboard, agents, tasks/approvals,
│   │   │                  prospects, clients, projects, campaigns, email, funnels, finance, reports)
│   │   ├── utils/         errors, logging, time
│   │   ├── middleware.py  request ID, security headers, access log
│   │   ├── cli.py         init-db, create-user
│   │   └── main.py        app factory
│   ├── migrations/        Alembic
│   └── tests/             pytest
├── frontend/
│   ├── index.html, login.html
│   └── assets/{css,js,vendor}   vanilla JS modules + Chart.js (vendored, works offline)
├── docs/                  plan, architecture, database design, API spec
├── scripts/               setup, create_founder, run_dev, test
├── data/ storage/         runtime files (git-ignored)
├── Dockerfile, docker-compose.yml, requirements*.txt, .env.example
```

## Design documents

- [Implementation plan and risks](docs/IMPLEMENTATION_PLAN.md)
- [Architecture and security model](docs/ARCHITECTURE.md)
- [Database design and KPI definitions](docs/DATABASE_DESIGN.md)
- [API specification](docs/API_SPECIFICATION.md)

## Known limitations

- Rate limiting is in-memory (sliding window), so it applies per process. Use a shared store if you run several workers.
- Invoices have no tax lines or currencies other than USD. Refunds are not modelled; delete or adjust
  the payment instead (Founder/Admin).
- SQLite stores `NUMERIC` as REAL; money is rounded to cents in Python. PostgreSQL stores exact values.
- No self-service forgotten-password flow (nothing is emailed). Use `app.cli reset-password`.
- No multi-factor authentication yet (see docs/SECURITY.md).
- The OpenAI and Anthropic adapters are tested against fake SDK clients only; no live call has
  been made from this machine (no keys configured).
- Tasks run one at a time on a single background thread. A long real-model request delays the
  next one; a worker queue can replace the runner later.
- The mock provider routes requests by keywords, so its choice of agents is approximate. Real
  models choose from the agent descriptions.
- Duplicate detection compares phone numbers in Python over at most 500 candidate rows, which is
  fine for a small agency's pipeline but would need an indexed phone column at larger scale.
- Funnel previews load in a sandboxed iframe. If your browser blocks it, use "Open preview in a new tab".
- AI drafts are linked to campaigns/funnels by scanning the latest 500 agent tasks; fine for one agency,
  would need an indexed link column at larger scale.
- Approving an action records the decision only. Sending, publishing and ad integrations arrive
  in later phases and will act only on approved requests.
