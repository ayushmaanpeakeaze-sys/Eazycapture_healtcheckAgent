# EazyCapture AI Agent

A multi-tenant **bookkeeping health-check** service for Xero ledgers. It pulls
an organisation's accounting data (via Nango → Xero), runs ~30 deterministic +
AI-assisted checks over it (duplicate invoices, missing tax codes, mis-coded
accounts, unreconciled bank items, bank-balance differences, …), and serves the
flagged issues — with one-click fix suggestions — to an accountant dashboard.

**Stack:** FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL · Celery · Redis ·
Nango (Xero) · Groq (LLM). Python 3.13.

---

## How it fits together

```
   Frontend (React)
        │  HTTPS + JWT
        ▼
   FastAPI  (:8001)  ── reads/writes ──▶  PostgreSQL (:5434)
        │   │
        │   └── enqueues background work ──▶  Celery worker ──▶ Redis (broker + cache)
        │                                          │
        └── calls ──▶ Nango cloud ──▶ Xero API     └── audits, syncs, AI enrichment
                         ▲
                         └── runs our deployed custom Actions (TypeScript)
```

- **FastAPI** handles every HTTP request and returns fast — anything slow
  (an audit, a data sync, LLM enrichment) is handed to **Celery**.
- **PostgreSQL** stores everything tenant-scoped: companies, synced Xero data,
  audit results, review notes.
- **Redis** is both the Celery message broker *and* a cache.
- **Nango** is the managed OAuth + API layer in front of Xero (it holds the
  Xero tokens and refreshes them; we never store Xero credentials).

---

## Folder structure

The codebase is a **3-layer architecture** — infrastructure, pure business
logic, and the web layer are kept separate. This is why the audit engine has
hundreds of unit tests that run with no server and no database.

```
app/
├── core/        🔧 Shared infrastructure
│                   db.py · config.py · auth.py · celery_app.py ·
│                   redis_client.py · multi_tenant.py
│
├── schemas/     📋 Pydantic data shapes (e.g. BatchTransaction)
│
├── checks/      ✅ The check rules, one module per category
│                   bank · tax · coding · dates · duplicates · approval ·
│                   contacts · documents · fixed_assets
│
├── services/    🧠 Pure business logic — NO web, NO framework deps
│   ├── healthcheck/   the audit ENGINE (orchestrator + deterministic checks)
│   └── insights/      KPI computation
│
├── modules/     🌐 The web/domain layer — FastAPI routers + DB models + Celery tasks
│   ├── auth/             users, login, JWT
│   ├── healthcheck/      audit domain: models, routers, tasks, domain services
│   ├── integrations/     external systems
│   │   ├── nango/        our backend's Nango/Xero client (Python)
│   │   └── sync/         DB-backed incremental Xero sync
│   ├── ai/              LLM enrichment + insights
│   ├── insights/        KPI snapshots
│   └── notifications/   email
│
└── api/         🚪 The stateless rules/LLM endpoints (e.g. /health-check/batch)

alembic/         Database migrations
tests/           Test suite (runs against Postgres + Redis)
nango-integrations/   Custom Xero Actions (TypeScript) — deployed to Nango (see below)
```

**Mental model:**
- `core` = the skeleton (infra everything depends on).
- `checks` + `services` = the **brain** — pure audit logic, framework-free, fully unit-tested.
- `modules` + `api` = the **body** — how the brain is exposed over HTTP, DB and background tasks.

### Why there are two "nango" folders

They are completely different things — not duplication:

| | `app/modules/integrations/nango/` | `nango-integrations/` (repo root) |
|---|---|---|
| Language | Python | TypeScript |
| Role | our backend's **client** — *calls* Nango | our custom **Actions** — code Nango *runs* for us |
| Runs where | inside this backend | on Nango's servers |
| Examples | `client.py`, `service.py` | `list-invoices-full.ts`, `update-invoice.ts` |

Flow: our backend (`service.py`) triggers an Action → Nango runs our deployed
TypeScript Action → the Action calls Xero and returns the data. The
`nango-integrations/` folder name + root location are **required by the Nango
CLI** (`nango deploy` reads from there), so it cannot live under `app/`.

---

## Why Celery (background workers)

Some operations are too slow to run inside an HTTP request:

- **Audits** — fetch a whole ledger, run ~30 checks, persist results.
- **Data sync** — pull invoices/bills/contacts/etc. from Xero into our DB.
- **AI enrichment** — LLM calls to explain each flagged issue.
- **Notifications** — sending email.

So the API endpoint just **enqueues** the job and returns `202` immediately
(with a `batch_id` to poll); the **Celery worker** does the heavy work in the
background. This keeps the API responsive and lets slow work retry on failure.

There are also **scheduled** jobs (Celery beat): a nightly Xero sync and a
nightly KPI snapshot.

## Why Redis (broker + cache)

Redis plays two roles:

1. **Celery broker + result backend** — the queue the API pushes jobs onto and
   the worker pulls from.
2. **Cache** — AI insights and audit-progress are cached so the dashboard reads
   them in milliseconds instead of recomputing or re-calling the LLM. Cached
   entries expire on a TTL.

## DB-backed sync (the data source)

Instead of re-fetching the entire Xero ledger on every audit, the service
mirrors Xero into company-scoped tables and keeps them fresh with an
**incremental sync** (Xero's `If-Modified-Since` watermark — only changed
records are pulled). Audits then read from the local DB.

- Configured by `AUDIT_SOURCE`: `proxy` (live proxy), `action` (live custom
  actions), or `db` (read from the synced tables — fast + resilient).
- Three sync modes: initial (on connect), nightly auto, and a manual
  "Refresh Data" button.

---

## Setup & run

**Prerequisites:** Python 3.13, Docker (for Postgres + Redis).

```bash
# 1. Install dependencies into a virtualenv
make install                      # creates .venv + installs requirements.txt

# 2. Configure
cp .env.example .env              # set GROQ_API_KEY + NANGO_SECRET_KEY

# 3. Start infrastructure (Postgres :5434 + Redis :6379)
make up

# 4. Run migrations
make migrate                      # alembic upgrade head

# 5. Start the API + the worker (separate terminals)
make api                          # uvicorn on :8001
make worker                       # Celery worker
```

Verify:

```bash
curl http://localhost:8001/health        # {"status":"ok"}
```

API docs (Swagger): <http://localhost:8001/docs>

The equivalent raw commands (what the `make` targets run):

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
.venv/bin/celery -A app.core.celery_app worker --loglevel=info --pool=solo
.venv/bin/celery -A app.core.celery_app beat        # scheduled jobs (nightly sync/snapshot)
```

### Useful commands

| Command | What it does |
|---|---|
| `make install` | Create `.venv`, install dependencies |
| `make up` / `make down` | Start / stop Postgres + Redis (Docker) |
| `make migrate` | `alembic upgrade head` |
| `make api` | Run FastAPI on `:8001` |
| `make worker` | Run the Celery worker |
| `make test` | Run the full test suite |
| `make psql` / `make redis-cli` | Shell into Postgres / Redis |

---

## Configuration (key env vars)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` / `CELERY_BROKER_URL` | Redis (cache + Celery) |
| `NANGO_SECRET_KEY` | Backend → Nango authentication |
| `NANGO_WEBHOOK_SECRET` | Verify incoming Nango webhooks |
| `GROQ_API_KEY` | LLM (enrichment + fix suggestions) |
| `AUDIT_SOURCE` | `proxy` \| `action` \| `db` — where audits read Xero data |
| `LLM_CHECKS_ENABLED` | Toggle the LLM finder passes (off = faster, deterministic only) |
| `JWT_SECRET` / `AUTH_DISABLED` | Auth — bearer-token verification |

---

## Multi-tenancy (strict)

Every tenant-scoped table carries `company_id` (NOT NULL, indexed), every
endpoint resolves + access-checks the company via `get_current_company_id`, and
every query filters on `company_id`. Cross-tenant leak guards are locked in by
tests against a live database.

---

## Testing

```bash
make test          # or: .venv/bin/python -m pytest -q
```

The audit engine (`app/checks`, `app/services`) is pure Python and tested
without any server, database, or network. Integration tests for routers,
multi-tenancy and the sync layer run against Postgres + Redis.
