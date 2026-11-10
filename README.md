# AgencyOS — AI Agency Operating System

Internal platform for running an AI-assisted marketing and automation agency: Founder dashboard,
AI CEO with specialised agents, CRM, projects, campaigns, funnels, finance and approvals.

**Status: Phases 1 (Foundation) and 2 (AI CEO) complete.** See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the phase roadmap.

| Works now | Coming next |
|---|---|
| Login/logout, roles, sessions, CSRF, rate limiting, audit log | Phase 3: Prospects (CSV), clients, projects |
| Founder dashboard: 11 KPIs, 5 charts, activity feeds — all from the database | Phase 4: Campaigns, email drafts, funnels, landing pages |
| **AI CEO chat**: persistent conversations; the CEO plans and delegates to specialists, live status per agent | Phase 5: Finance and analytics |
| **11 agents** (CEO + 10 specialists) with editable prompts/models, custom agents, test runs, execution history | Phase 6: Production hardening |
| **Task orchestration**: state machine, retries, cancellation, delegation limits, token + cost tracking | |
| **Approvals**: every external action an agent proposes waits for a Founder/Admin decision | |
| Providers: offline Mock (default), OpenAI, Anthropic | |

Sidebar items for unbuilt modules are marked `P3`–`P5`; they open a page that says the module is
not built yet. There are no placeholder buttons.

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
./scripts/test.sh
```

Tests use a fresh temporary SQLite database for each test and never call external services.

## Docker

```bash
cp .env.example .env    # set SECRET_KEY first
docker compose up --build
```

The container applies migrations on start and keeps the database in the `agencyos-data` volume.
Create the Founder login inside the container:

```bash
docker compose exec agencyos python -m app.cli create-user --email you@youragency.com --name "Your Name" --role FOUNDER
```

> Docker is not installed on the development Mac this was built on, so the Docker path has not
> been run yet. The native Python path is fully tested.

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
| `ENABLE_DEMO_LOGIN` | `false` | Development only |

## Project layout

```
agencyos/
├── backend/
│   ├── app/
│   │   ├── api/v1/        route handlers (auth, dashboard, health, settings, ceo, agents, tasks, approvals)
│   │   ├── agents/        BaseAgent, CEOAgent + 10 specialists, registry, Pydantic I/O schemas
│   │   ├── orchestrator/  state machine, delegation policy, engine, runners
│   │   ├── providers/     mock, OpenAI, Anthropic adapters; pricing
│   │   ├── config/        settings (pydantic-settings)
│   │   ├── database/      engine, session, base + mixins
│   │   ├── models/        SQLAlchemy models for all 26 tables
│   │   ├── schemas/       Pydantic request/response models
│   │   ├── security/      passwords, tokens, rate limiter, auth dependencies
│   │   ├── services/      business logic (auth, audit, dashboard, agents, tasks/approvals)
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

- Rate limiting is in-memory, so it applies per process. Use a shared store if you run several workers.
- SQLite stores `NUMERIC` as REAL; money is rounded to cents in Python. PostgreSQL stores exact values.
- No forgotten-password flow yet (nothing is emailed in v1). Signed-in users can change their own
  password under Settings; an admin reset command is planned for Phase 6.
- Docker files are written but not yet run on this machine.
- The OpenAI and Anthropic adapters are tested against fake SDK clients only; no live call has
  been made from this machine (no keys configured).
- Tasks run one at a time on a single background thread. A long real-model request delays the
  next one; a worker queue can replace the runner later.
- The mock provider routes requests by keywords, so its choice of agents is approximate. Real
  models choose from the agent descriptions.
- Approving an action records the decision only. Sending, publishing and ad integrations arrive
  in later phases and will act only on approved requests.
