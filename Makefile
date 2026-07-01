.PHONY: help install up down logs psql redis-cli migrate revision api worker seed test reset-demo

PYTHON  ?= .venv/bin/python
PIP     ?= .venv/bin/pip
UVICORN ?= .venv/bin/uvicorn
ALEMBIC ?= .venv/bin/alembic
CELERY  ?= .venv/bin/celery
PORT    ?= 8001

help:
	@echo "Targets:"
	@echo "  install     Create .venv + install requirements.txt"
	@echo "  up          Start Postgres + Redis (docker compose)"
	@echo "  down        Stop Postgres + Redis"
	@echo "  logs        Tail docker compose logs"
	@echo "  psql        psql into the POC Postgres (eaz-postgres)"
	@echo "  redis-cli   redis-cli into eaz-redis"
	@echo "  migrate     alembic upgrade head"
	@echo "  revision    NAME=... generate a new alembic revision"
	@echo "  api         uvicorn app.main:app --reload on :$(PORT)"
	@echo "  worker      celery worker for the healthcheck_poc app"
	@echo "  seed        Run the demo seed (idempotent)"
	@echo "  test        pytest"

install:
	python3.13 -m venv .venv || python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

up:
	docker compose up -d postgres redis

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

psql:
	docker exec -it eaz-postgres psql -U $${POSTGRES_USER:-hcpoc} -d $${POSTGRES_DB:-healthcheck_poc}

redis-cli:
	docker exec -it eaz-redis redis-cli -a $${REDIS_PASSWORD:-peakeaze-redis}

migrate:
	$(ALEMBIC) upgrade head

revision:
	@test -n "$(NAME)" || (echo "Usage: make revision NAME=add_xyz"; exit 1)
	$(ALEMBIC) revision -m "$(NAME)"

api:
	$(UVICORN) app.main:app --host 0.0.0.0 --port $(PORT) --reload

# Default-queue worker (sync + audits + insights). Solo pool on macOS to
# avoid the fork-vs-objc quirk; prefork in production.
worker:
	$(CELERY) -A app.core.celery_app worker -Q celery --loglevel=info --pool=solo

# LLM-enrichment worker (isolated 'enrich' queue) — run alongside `worker`.
worker-enrich:
	$(CELERY) -A app.core.celery_app worker -Q enrich --loglevel=info --pool=solo

seed:
	$(PYTHON) -m app.modules.healthcheck.seed_data

test:
	$(PYTHON) -m pytest tests/ -v

# Demo prep — wipes Demo Co's audit state, re-runs the audit, and
# fires the re-enrich sweep so every trapped row ends up with an AI
# annotation. Requires the API + Celery worker to be running.
reset-demo:
	$(PYTHON) -m scripts.reset_demo

# Toggle Demo Co between known Xero orgs. ``make profile`` lists the
# available profiles + which one is currently active; the variants
# below switch in one command.
profile:
	$(PYTHON) -m scripts.switch_profile list

profile-sir:
	$(PYTHON) -m scripts.switch_profile sir-test

profile-xero-demo:
	$(PYTHON) -m scripts.switch_profile xero-demo
