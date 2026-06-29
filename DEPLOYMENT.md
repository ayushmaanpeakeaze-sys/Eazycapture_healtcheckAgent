# Deployment

The stack is a modular-monolith FastAPI app plus a Celery worker and beat,
backed by Postgres and Redis. One Docker image runs all three roles (API,
worker, beat) — they differ only by the command they start with.

## Run the full stack locally (Docker)

```bash
cp .env.example .env        # fill in real values
docker compose up --build
```

Compose brings up, in order:

| Service    | Role                                             |
|------------|--------------------------------------------------|
| `postgres` | database                                         |
| `redis`    | Celery broker/result + caches + rate-limit       |
| `migrate`  | one-shot `alembic upgrade head` (runs to completion first) |
| `agent`    | FastAPI API on `:8001`                           |
| `worker`   | Celery worker — background audits + AI enrichment |
| `beat`     | Celery beat — scheduled jobs (daily Xero reconcile) |

`agent`/`worker`/`beat` wait for `migrate` to finish, so the schema is always
current before anything serves traffic. In-container DB/Redis URLs are
overridden to use service names (`postgres`, `redis`) while `.env` keeps the
`127.0.0.1` URLs for running uvicorn directly on your machine.

## Deploy to Render (one-click blueprint)

The repo ships a [`render.yaml`](render.yaml) blueprint that provisions and
wires the whole stack. **Every push to the connected branch auto-deploys
(this is the CD that pairs with the CI in `.github/workflows/ci.yml`).**

**Steps:**

1. Push this repo to GitHub (already done).
2. Render Dashboard → **New** → **Blueprint** → connect this repo → **Apply**.
   Render reads `render.yaml` and creates:
   | Service | Role |
   |---|---|
   | `eazycapture-api` | FastAPI (public URL) |
   | `eazycapture-worker` | Celery worker (audits, sync, enrichment) |
   | `eazycapture-beat` | Celery beat (nightly sync + KPI snapshot) |
   | `eazycapture-db` | managed PostgreSQL |
   | `eazycapture-redis` | managed Redis (cache + Celery broker) |
3. Set the **secret** env vars (marked `sync: false`) in the dashboard — these
   are never committed:
   - `GROQ_API_KEY`
   - `NANGO_SECRET_KEY`
   - `NANGO_WEBHOOK_SECRET`
   - `CORS_ALLOWED_ORIGINS` — your frontend origin(s), comma-separated
   (`JWT_SECRET` is auto-generated; `DATABASE_URL` / `REDIS_URL` / Celery URLs
   are wired automatically from the DB + Redis services.)
4. First deploy runs `alembic upgrade head` automatically (`preDeployCommand`).

**After it's live:**

- The API has a public URL (e.g. `https://eazycapture-api.onrender.com`). Point
  the **Nango webhook** at `…/api/v1/webhooks/nango` — no tunnel/ngrok needed.
- Set `AUDIT_SOURCE=db` (already in the blueprint) — audits read the synced
  tables, so they're fast and survive a dead live token.

**Notes:**

- The `free` Postgres/Redis/web plans are fine for a demo but spin down on
  inactivity and the free DB expires — move to paid plans for production.
- If your Render account uses the newer **Key Value** product, change
  `type: redis` → `type: keyvalue` in `render.yaml`.

## Environments

`APP_ENV` selects the environment: `development` (default) | `staging` |
`production`. When `APP_ENV=production` the app **refuses to boot** if:

- `AUTH_DISABLED=true`, or
- `JWT_SECRET` is empty / weak (< 32 chars or contains `change-me`, `dev-`, …), or
- `DATABASE_URL` is unset.

This guard runs at import, so the API, Celery worker, beat, and Alembic are
all protected — a misconfigured production deploy crashes on startup instead
of silently running insecure.

## Production checklist

- [ ] `APP_ENV=production`
- [ ] `AUTH_DISABLED=false`
- [ ] Strong `JWT_SECRET` — `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] **Secrets in a manager** (AWS Secrets Manager / Vault / SSM), not committed `.env`
- [ ] **Managed Postgres** with automated backups + PITR (not the compose container)
- [ ] **Managed Redis** with a password + persistence
- [ ] **TLS at the proxy** (nginx / Traefik / cloud LB) terminating HTTPS in front of `:8001`
- [ ] `CORS_ALLOWED_ORIGINS` set to the real frontend origin(s)
- [ ] **Transactional email provider** (Resend / SendGrid / SES) instead of personal Gmail,
      `SMTP_FROM` on your own domain, and `EMAIL_WEBHOOK_SECRET` set with the
      provider's webhook pointed at `POST /api/v1/webhooks/email`
- [ ] **Error tracking** (e.g. Sentry) + log aggregation + uptime/health alerting
      (health probe: `GET /health`)
- [ ] Run `alembic upgrade head` on deploy (the `migrate` service does this)

## Migrations

```bash
alembic upgrade head          # apply
alembic revision --autogenerate -m "describe change"   # create (review before commit)
```

Models live per-module (`app/modules/*/models.py`); `alembic/env.py` imports
all of them so autogenerate sees every table.
