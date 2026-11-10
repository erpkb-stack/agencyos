# AgencyOS — Database Design

SQLAlchemy 2.x ORM, Alembic migrations, SQLite now / PostgreSQL-compatible.

## Conventions

- Integer surrogate primary keys (`id`).
- `created_at` / `updated_at` (UTC, timezone-aware) on every mutable table.
- `deleted_at` soft delete on business records users may "delete" (prospects, clients, projects,
  campaigns, email templates, funnels, expenses, conversations). Queries exclude soft-deleted rows.
- Enumerations stored as `VARCHAR` + `CHECK` constraint (`Enum(native_enum=False)`), portable to
  PostgreSQL without enum migrations.
- Money: `Numeric(12, 2)`. SQLite stores these as REAL, so services always quantize to cents with
  `Decimal`; PostgreSQL stores exact `NUMERIC`. API cost uses `Numeric(12, 6)`.
- JSON columns (`JSON` type) for agent inputs/outputs, generated content and configuration.
- Foreign keys enforced on SQLite (`PRAGMA foreign_keys=ON`).
- Only business contact data the Founder enters or imports is stored. No personal data beyond
  what a B2B CRM needs; no payment card data at all.

## Tables

### Identity & audit

| Table | Key columns |
|---|---|
| `users` | email (unique), full_name, password_hash, role (FOUNDER/ADMIN/STAFF/READ_ONLY), is_active, last_login_at |
| `user_sessions` | user_id→users, token_hash (unique, SHA-256), expires_at, last_seen_at, revoked_at, ip_address, user_agent |
| `audit_logs` | user_id→users (nullable), actor_type (user/agent/system), action, entity_type, entity_id, details JSON, ip_address. Indexed on created_at and (entity_type, entity_id) |

### AI

| Table | Key columns |
|---|---|
| `agents` | key (unique, e.g. `sales`), name, department, description, system_prompt, capabilities JSON, allowed_tools JSON, model_provider, model_name, temperature, is_active, last_execution_at |
| `conversations` | user_id→users, title, deleted_at |
| `messages` | conversation_id→conversations, role (user/assistant/system), content, task_id→agent_tasks (nullable), meta JSON |
| `agent_tasks` | parent_task_id→agent_tasks, conversation_id, agent_id, project_id, created_by_user_id, title, task_type, priority (LOW/NORMAL/HIGH/URGENT), status (PENDING/ASSIGNED/RUNNING/AWAITING_APPROVAL/COMPLETED/FAILED/CANCELLED), input JSON, output JSON, error, attempts, max_attempts, depth, requires_approval, started_at, completed_at |
| `agent_executions` | task_id→agent_tasks, agent_id→agents, status (RUNNING/SUCCEEDED/FAILED), provider, model, input JSON, output JSON, error, prompt_tokens, completion_tokens, estimated_cost, duration_ms, started_at, finished_at |

### CRM

| Table | Key columns |
|---|---|
| `prospects` | company_name, website, normalized_domain (indexed, dedupe), industry, address, city, state, zip_code, phone, email, contact_name, contact_title, lead_source, observations, opportunity, lead_score (0–100 CHECK), stage (NEW…LOST), last_contacted_at, next_follow_up_on, notes, deleted_at |
| `prospect_activities` | prospect_id→prospects, activity_type, description, user_id, agent_id |
| `clients` | prospect_id→prospects (nullable), company_name, contact_name, email, phone, website, industry, address, contract_status (PROSPECTIVE/ACTIVE/PAUSED/ENDED), start_date, end_date, service_package, monthly_fee, setup_fee, notes, deleted_at |

Sales stages, in order: NEW, RESEARCHED, QUALIFIED, CONTACTED, RESPONDED, MEETING_SCHEDULED,
PROPOSAL_SENT, NEGOTIATION, WON, LOST.

### Delivery

| Table | Key columns |
|---|---|
| `projects` | client_id→clients, name, description, scope, start_date, due_date, budget, status (PLANNED/IN_PROGRESS/REVIEW/AWAITING_CLIENT/COMPLETED/CANCELLED), completion_percent (0–100 CHECK), deleted_at |
| `project_agents` | project_id→projects, agent_id→agents (composite PK) |
| `project_tasks` | project_id→projects, title, description, status (TODO/IN_PROGRESS/BLOCKED/DONE), assignee_user_id, assignee_agent_id, depends_on_task_id→project_tasks, due_date, completed_at |
| `project_comments` | project_id, project_task_id (nullable), user_id, body |
| `deliverables` | project_id→projects, title, description, status (DRAFT/IN_REVIEW/APPROVED/DELIVERED), url, file_path, due_date, delivered_at |

### Marketing

| Table | Key columns |
|---|---|
| `campaigns` | client_id (nullable), name, campaign_type (COLD_EMAIL/PROMOTIONAL_EMAIL/NEWSLETTER/GOOGLE_ADS/META_ADS/LEAD_NURTURING/REENGAGEMENT), target_audience, objectives, budget, start_date, end_date, status (DRAFT/AWAITING_APPROVAL/APPROVED/ACTIVE/PAUSED/COMPLETED/CANCELLED), generated_content JSON, metrics JSON, deleted_at |
| `email_templates` | client_id, campaign_id (nullable), name, kind (TEMPLATE/DRAFT/SEQUENCE_STEP), subject, body_text, sequence_order, delay_days, status (DRAFT/AWAITING_APPROVAL/APPROVED), deleted_at |
| `email_suppressions` | email (unique), reason (UNSUBSCRIBED/BOUNCED/COMPLAINT/MANUAL), source |
| `funnels` | client_id (nullable), name, industry, template_key, landing_page_url, target_conversion, lead_fields JSON, call_to_action, form_config JSON, follow_up_workflow JSON, generated_html, status (DRAFT/AWAITING_APPROVAL/APPROVED/PUBLISHED), deleted_at |

### Approvals

| Table | Key columns |
|---|---|
| `approvals` | action_type (SEND_EMAIL/LAUNCH_ADS/PUBLISH_WEBSITE/CHANGE_BUDGET/SPEND_MONEY/ISSUE_REFUND/SIGN_CONTRACT/DELETE_RECORD/OTHER), title, requested_by_agent_id, requested_by_user_id, client_id, task_id, payload JSON, status (PENDING/APPROVED/REJECTED/EXPIRED), decided_by_user_id, decided_at, comments, expires_at |

### Finance

| Table | Key columns |
|---|---|
| `invoices` | client_id→clients, number (unique), status (DRAFT/SENT/PARTIALLY_PAID/PAID/VOID), issue_date, due_date, subtotal, total, notes |
| `invoice_items` | invoice_id→invoices, description, quantity, unit_price, amount |
| `payments` | invoice_id→invoices, client_id, amount, paid_on, method, reference |
| `revenue` | client_id, payment_id→payments (nullable), category (SETUP_FEE/RETAINER/ADDITIONAL_SERVICE/OTHER), description, amount, received_on |
| `expenses` | category (AI_API/HOSTING/DOMAINS/MARKETING_TOOLS/SOFTWARE/OTHER), vendor, description, amount, incurred_on, is_recurring, deleted_at |

An invoice becomes PAID only when the sum of its `payments` rows ≥ its total. Revenue is money
received (cash basis): recording a payment creates the linked `revenue` row.

## Dashboard metric definitions

| KPI | Definition |
|---|---|
| Total revenue | `SUM(revenue.amount)` |
| MRR | `SUM(clients.monthly_fee)` where contract_status = ACTIVE |
| Total / active clients | non-deleted clients / those ACTIVE |
| Total prospects | non-deleted prospects |
| Qualified leads | prospects whose stage is QUALIFIED or later, excluding LOST |
| Discovery meetings | stage MEETING_SCHEDULED or later, excluding LOST |
| Proposals sent | stage PROPOSAL_SENT, NEGOTIATION or WON |
| Closed deals | stage WON |
| Active projects | status IN_PROGRESS, REVIEW or AWAITING_CLIENT |
| Pending approvals | approvals with status PENDING |

## Migrations

```bash
cd backend
../.venv/bin/alembic upgrade head          # apply
../.venv/bin/alembic revision --autogenerate -m "describe change"   # after editing models
```

A test (`tests/test_migrations.py`) applies all migrations to a fresh database and fails if the
models and migrations drift apart.

**PostgreSQL:** set `DATABASE_URL=postgresql+psycopg://user:pass@host/agencyos`, install
`psycopg[binary]`, run `alembic upgrade head`. No model changes are needed.
