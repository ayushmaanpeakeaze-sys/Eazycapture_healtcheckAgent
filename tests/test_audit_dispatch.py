"""Day 3 — audit dispatch end-state test.

We don't run the full Celery pipeline here (that needs a live worker
+ rules service); we just verify the dispatch endpoint creates the
right Postgres + Redis state synchronously, and that the Celery enqueue
call is made. ``.delay()`` is monkey-patched so the test doesn't enqueue
a real task that would later fail outside the worker.

The tests use ``httpx.AsyncClient`` + ASGITransport (not ``TestClient``)
so every async call shares pytest-asyncio's session event loop — which
matters because the global SQLAlchemy async engine pools connections
per loop and mixing loops trips a "Future attached to a different loop"
guard.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid

import httpx
import pytest
import redis as sync_redis
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import AuditBatch, Company
from app.modules.healthcheck.seed_data import DEMO_CO_ID
from app.modules.healthcheck.services.audit_service import batch_key


@pytest.fixture(scope="session", autouse=True)
def _seed_demo_co_once():
    """Run the seed in a child process so its async engine state can't
    leak into the test session's async engine. The seed module is
    idempotent — re-running between sessions is a no-op."""
    result = subprocess.run(
        [sys.executable, "-m", "app.modules.healthcheck.seed_data"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"seed subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.fixture
def _stub_celery_delay(monkeypatch):
    """Replace .delay() with a no-op so the dispatch doesn't enqueue a
    real Celery task during the test run."""
    calls: list[tuple[tuple, dict]] = []

    def _record(*args, **kwargs):
        calls.append((args, kwargs))

    from app.modules.healthcheck import tasks
    monkeypatch.setattr(tasks.historical_audit_task, "delay", _record)
    return calls


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


def _get_audit_batch_sync(batch_id: uuid.UUID) -> AuditBatch:
    with SyncSessionLocal() as db:
        row = db.execute(select(AuditBatch).where(AuditBatch.id == batch_id))
        return row.scalar_one()


def _company_exists_sync(company_id: uuid.UUID) -> bool:
    with SyncSessionLocal() as db:
        return (
            db.execute(select(Company.id).where(Company.id == company_id))
            .scalar_one_or_none()
            is not None
        )


async def test_dispatch_creates_batch_and_seeds_redis(
    async_client: httpx.AsyncClient,
    _stub_celery_delay,
):
    assert _company_exists_sync(DEMO_CO_ID), "seed fixture should have created Demo Co"

    resp = await async_client.post(
        f"/api/v1/health/sync-xero-history/{DEMO_CO_ID}/",
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    batch_id = uuid.UUID(body["batch_id"])
    assert body["status"] == "in_progress"

    # 1. AuditBatch row exists in Postgres, status in_progress, scoped to Demo Co.
    batch = _get_audit_batch_sync(batch_id)
    assert batch.company_id == DEMO_CO_ID
    assert batch.status == "in_progress"
    assert batch.total == 0
    assert batch.trapped == 0

    # 2. Redis meta hash seeded with stage=dispatched.
    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        raw = r.hget(batch_key(batch_id), "_meta")
        assert raw is not None, "Redis _meta hash was not seeded"
        meta = json.loads(raw)
        assert meta["status"] == "in_progress"
        assert meta["stage"] == "dispatched"
        assert meta["company_id"] == str(DEMO_CO_ID)
    finally:
        r.close()

    # 3. The Celery .delay was called once with
    #    (batch_id, company_id, date_from, date_to). No period → dates None.
    assert len(_stub_celery_delay) == 1
    args, _ = _stub_celery_delay[0]
    assert args[0] == str(batch_id)
    assert args[1] == str(DEMO_CO_ID)
    assert args[2] is None  # date_from
    assert args[3] is None  # date_to


async def test_dispatch_404_for_unknown_company(
    async_client: httpx.AsyncClient,
    _stub_celery_delay,
):
    """Multi-tenant guard: dispatch against a non-existent company → 404,
    no Celery enqueue."""
    bogus = uuid.uuid4()
    resp = await async_client.post(
        f"/api/v1/health/sync-xero-history/{bogus}/",
    )
    assert resp.status_code == 404
    assert _stub_celery_delay == []


async def test_status_404_for_unknown_batch(async_client: httpx.AsyncClient):
    bogus = uuid.uuid4()
    resp = await async_client.get(
        f"/api/v1/health/sync-xero-history-status/{bogus}/",
    )
    assert resp.status_code == 404
