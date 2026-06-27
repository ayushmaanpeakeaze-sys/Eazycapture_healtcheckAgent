# EazyCapture AI Agent

FastAPI service that powers EazyCapture's bookkeeping health-check.
Two roles in one process:

1. **AI service** (existing, stateless) — pre-ledger invoice validation,
   post-ledger batch audit, LLM enrichment + fix suggestions. No DB.
2. **Healthcheck POC** (new, DB-backed) — multi-tenant audit workflow:
   own Postgres, Celery workers, Nango → Xero proxy, all under
   `/api/v1/health/*`. Calls back into the AI routes above for rules
   + LLM, never duplicates that logic.

## Architecture

```
React UI (:3000)              Django (existing web app)
      │                              │
      └─→ /api/v1/health/*           └─→ HTTP ──────────────┐
                                                            ▼
                                                FastAPI (this service, :8001)
                                                  │
                                                  ├── /api/v1/{validate-invoice,
                                                  │             health-check,
                                                  │             enrich-audit,
                                                  │             enrich-row,
                                                  │             suggest-fix}
                                                  │      stateless · Groq · Redis writes
                                                  │
                                                  └── /api/v1/health/*  (POC)
                                                       Postgres (:5434) + Celery
                                                       Nango proxy (when configured)
```

The Django monolith is untouched. This service does **not** depend on
it at runtime — and the POC stack runs end-to-end on seeded data without
any external Xero connection.

## Stack

- FastAPI 0.115 + Uvicorn
- SQLAlchemy 2.0 (async) + asyncpg, psycopg (Alembic only)
- Alembic 1.14 — migrations
- Pydantic v2 + pydantic-settings
- Celery 5.4 + Redis 7 — background tasks (audit, re-enrich)
- httpx — async HTTP client (Nango proxy, AI FastAPI calls)
- PyJWT — optional bearer-token verification
- Postgres 17 — own DB on `:5434`
- Groq (`openai/gpt-oss-120b`) — LLM for enrichment + fix suggestions

## What's in this POC (7-day build)

| Day | Capability | File highlights |
|---|---|---|
| 1 | Postgres + Alembic + 5 SQLAlchemy 2.0 models + `/health` | `app/modules/healthcheck/models.py`, `alembic/versions/20260527_0001_initial_schema.py` |
| 2 | Seed (Demo Co + Test Co, pinned UUIDs) + multi-tenant guard | `seed_data.py`, `app/core/multi_tenant.py`, `repository.py` |
| 3 | Audit dispatch + Celery task + Redis progress meta | `services/audit_service.py`, `tasks.py` |
| 4 | Trapped-invoices feed (DB query + Redis MGET + `xero_url`) | `services/trapped_service.py`, `xero_links.py` |
| 5 | Resolve / dismiss / suggest-fix / apply-ai-fix | `services/{resolve,suggest_fix,apply_ai_fix}_service.py` |
| 6 | Nango integration with graceful stub fallback + webhook | `app/modules/integrations/nango/` |
| 7 | Panorama + summary + re-enrich + optional JWT + demo prep | `services/panorama_service.py`, `core/auth.py`, `scripts/reset_demo.py` |

**30/30 tests passing.**

## Run from scratch (5 commands)

```bash
make install                  # creates .venv and installs requirements.txt
cp .env.example .env          # fill in GROQ_API_KEY; the rest works out of the box
make up                       # Postgres :5434 + Redis :6379 in docker compose
make migrate                  # alembic upgrade head (creates the 5 POC tables)
make api & make worker        # FastAPI :8001 + Celery worker (solo pool on macOS)
```

Verify:

```bash
curl http://localhost:8001/health
# {"status":"ok"}
make psql -c "\dt"            # alembic_version + 5 healthcheck tables
```

## How to demo

```bash
make reset-demo                # wipes Demo Co, re-audits, re-enriches → 15/15 ready
```

Then follow [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — 10 steps,
copy-paste curl commands, expected outputs, and prepared answers for
sir's predictable questions.

If the UI still prompts for a token, set `AUTH_DISABLED=true` in `.env`
and restart the API. That forces the open demo path even if a JWT secret
is set somewhere else.

## How to go live (post-POC)

| When | Set | Effect |
|---|---|---|
| Sir hands over Nango secret | `NANGO_SECRET_KEY=secret_xxx` + `NANGO_WEBHOOK_SECRET=...` | Audit reads from real Xero; resolves PUT to real Xero |
| Frontend onboards a tenant | Calls `POST /api/v1/integrations/nango/connect-session/` → OAuth popup → Nango webhook → `Company.{nango_connection_id, xero_tenant_id}` set | That tenant flips to live mode (others stay on seed) |
| Production integrates with Django auth | `JWT_SECRET=<same as Django>` | Every `/api/v1/health/*` route requires a bearer token; same tokens validate across services |

No code change required for any of these.

## API reference

### Stateless AI service (unchanged from before the POC)

| Method | Path |
|---|---|
| POST | `/api/v1/validate-invoice` |
| POST | `/api/v1/health-check/batch` |
| POST | `/api/v1/health-check/batch/async` |
| GET  | `/api/v1/audit/progress/{batch_id}` (SSE) |
| POST | `/api/v1/enrich-audit` |
| POST | `/api/v1/enrich-row` |
| POST | `/api/v1/suggest-fix` |

### Healthcheck POC (`/api/v1/health/*`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/sync-xero-history/{company_id}/` | Dispatch a historical audit (returns `batch_id`, 202) |
| GET  | `/sync-xero-history-status/{batch_id}/` | Poll batch status (Redis-only, frontend-friendly) |
| GET  | `/trapped-invoices/?company_id=…&limit=N&offset=N&search=…` | Paginated trapped-rows feed + AI splice + `xero_url` |
| POST | `/trapped/{row_id}/resolve/?company_id=…` | Apply field updates + mark resolved (stub or Nango) |
| POST | `/trapped/{row_id}/dismiss/?company_id=…` | Mark as false positive |
| GET  | `/trapped/{row_id}/suggest-fix/?company_id=…` | Proxy to AI suggest-fix |
| POST | `/trapped/{row_id}/apply-ai-fix/?company_id=…` | Pull suggestion, parse, mark resolved |
| GET  | `/companies-panorama/?days=30` | Cross-tenant health-score dashboard |
| GET  | `/summary/?company_id=…&days=30` | Per-company health summary + top issues |
| POST | `/re-enrich/?company_id=…` | Backfill AI annotations missing in Redis |

### Nango (auth + webhook)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/integrations/nango/connect-session/?provider=xero` | Start OAuth popup |
| POST | `/api/v1/webhooks/nango` | Receive `auth.creation` etc., link Company to Nango connection |

## Multi-tenancy rules (strict)

1. Every tenant-scoped table has `company_id` (`NOT NULL`, indexed).
2. Every router endpoint that touches tenant data depends on
   `get_current_company_id`.
3. Every repository method takes `company_id` as a required
   parameter.
4. Every SELECT includes `.where(Model.company_id == company_id)` —
   even single-row fetches by primary key.

The 4 multi-tenant tests in `tests/test_multi_tenant.py` lock these
in against a live Postgres.

## Folder layout

```
app/
  main.py                          FastAPI app + lifespan + CORS + auth-router
  core/
    config.py                      Frozen Settings dataclass
    db.py                          Async + sync SQLAlchemy engines
    auth.py                        Optional JWT bearer dep
    llm.py                         Shared Groq AsyncGroq singleton
    redis_client.py                Shared async Redis singleton
    multi_tenant.py                get_current_company_id dependency
    celery_app.py                  Celery app for healthcheck_poc
  schemas/                         AI-service request/response models
  api/                             AI-service routers
  services/                        AI-service business logic
  modules/
    healthcheck/                   POC business surface
      models.py                    5 SQLAlchemy 2.0 models
      schemas.py                   POC request/response models
      repository.py                Repo (company_id-scoped)
      routers.py                   POC HTTP routes (auth-gated)
      tasks.py                     Celery: historical_audit + reenrich_missing
      _fixtures.py                 Hardcoded COA + tax rates (swapped by Day 6)
      xero_links.py                Deep-link helper
      seed_data.py                 Demo Co + Test Co fixtures
      services/
        audit_service.py
        trapped_service.py
        resolve_service.py
        suggest_fix_service.py
        apply_ai_fix_service.py
        panorama_service.py
        reenrich_service.py
    integrations/nango/
      client.py                    Only file that calls api.nango.dev
      service.py                   Only allowed Nango interface
      routers.py                   Connect-session + webhook
alembic/                           Migrations
scripts/
  reset_demo.py                    `make reset-demo`
docs/
  DEMO_SCRIPT.md                   Sir-walkthrough script
tests/                             30 tests against live Postgres + Redis
Dockerfile
docker-compose.yml                 agent + redis + postgres
Makefile
pytest.ini
requirements.txt
.env.example
```

## What's NOT in this POC

Explicit list of polish items deferred to post-merge work — sir will
ask:

- **Per-company rate limiting** — currently any tenant can issue
  unlimited audit / re-enrich requests.
- **Structured logging / observability** — Sentry, Datadog, JSON
  logs, request-id tracing.
- **CI/CD pipeline** — no `.github/workflows`, no automated deploy.
- **Database backups** — local Postgres, no PITR / WAL archival.
- **SSL termination** — uvicorn on plain HTTP; production wants a
  reverse proxy.
- **Real auth integration** — JWT verification is in, but no
  signup/login flows; tokens come from Django.
- **Idempotency keys** — re-issuing the same resolve doesn't dedupe
  beyond the "already resolved" guard.
- **Per-row error UX** — bulk-resolve, undo, audit history view.

Standard SRE / product polish, ~1 week of work. The POC validates
that the design works.

## Test coverage

```
30 passed in ~3s
```

- 6 smoke tests (existing AI service routes — never broken)
- 4 multi-tenant tests (cross-tenant leak guards against live Postgres)
- 3 audit dispatch tests
- 3 trapped-invoices tests
- 4 resolve / dismiss / apply-ai-fix tests
- 5 Nango + webhook tests
- 2 panorama tests
- 1 summary test
- 2 auth tests

Run with `make test` or `.venv/bin/python -m pytest tests/ -v`.

## Useful commands

| Target | What it does |
|---|---|
| `make install` | Create `.venv` and install requirements |
| `make up` | Start Postgres + Redis (Docker) |
| `make down` | Stop Postgres + Redis |
| `make logs` | Tail docker compose logs |
| `make psql` | psql into the POC Postgres |
| `make redis-cli` | redis-cli into the local Redis |
| `make migrate` | `alembic upgrade head` |
| `make revision NAME=...` | New migration skeleton |
| `make api` | uvicorn on `:8001` with `--reload` |
| `make worker` | Celery worker (solo pool on macOS) |
| `make seed` | Idempotent demo seed |
| `make reset-demo` | Wipe + re-audit + re-enrich → 15/15 trapped + enriched |
| `make test` | Full pytest run |

## Known debt

- `services/ai_service.py` is ~1400 lines and bundles all rules + the
  category LLM. A clean split into `services/rules/*` is a high-value
  follow-up but needs careful migration of shared constants.
- Async-batch progress for `/api/v1/health-check/batch/async` is kept
  in-process. Single-worker setups are fine; multi-worker needs the
  `_batches` dict moved to Redis.
