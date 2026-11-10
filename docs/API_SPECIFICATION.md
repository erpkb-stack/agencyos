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

## Planned endpoints (later phases)

| Prefix | Phase | Main operations |
|---|---|---|
| `/campaigns` | 4 | CRUD, `POST /{id}/generate`, `POST /{id}/request-approval` |
| `/emails` | 4 | templates CRUD, `POST /sequences/generate`, suppression list, `POST /unsubscribe` |
| `/funnels` | 4 | CRUD, `GET /templates`, `POST /{id}/generate`, `GET /{id}/preview` |
| `/finance` | 5 | revenue, expenses, invoices (+items), payments, `GET /summary`, `GET /reports/monthly` |
| `/analytics` | 5 | daily / weekly / monthly reports, agent performance, `?format=csv` export |
