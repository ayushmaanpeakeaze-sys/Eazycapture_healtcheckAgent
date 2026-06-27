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
