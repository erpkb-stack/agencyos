# AgencyOS — API Specification

Base path: `/api/v1`. Interactive OpenAPI docs: `http://localhost:8000/docs`.

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

## Planned endpoints (later phases)

| Prefix | Phase | Main operations |
|---|---|---|
| `/prospects` | 3 | CRUD (soft delete), search/filter/sort, `POST /import` (CSV), `GET /export`, `GET /{id}/activities`, `POST /{id}/activities`, duplicate check |
| `/clients` | 3 | CRUD, `GET /{id}/projects`, `/invoices`, `/activity` |
| `/projects` | 3 | CRUD, tasks, dependencies, comments, deliverables, timeline |
| `/approvals` | 3 | `GET /{id}`, links to clients; approvals raised by marketing modules |
| `/campaigns` | 4 | CRUD, `POST /{id}/generate`, `POST /{id}/request-approval` |
| `/emails` | 4 | templates CRUD, `POST /sequences/generate`, suppression list, `POST /unsubscribe` |
| `/funnels` | 4 | CRUD, `GET /templates`, `POST /{id}/generate`, `GET /{id}/preview` |
| `/finance` | 5 | revenue, expenses, invoices (+items), payments, `GET /summary`, `GET /reports/monthly` |
| `/analytics` | 5 | daily / weekly / monthly reports, agent performance, `?format=csv` export |
