"""Repository-level proof that ``company_id`` is the only thing
separating tenants.

Two companies, each with a single trapped row. Every assertion below
guards a specific cross-tenant leak the spec forbids:

* ``list_post_ledger_trapped(co_a)`` must not return Co B's row.
* ``find_by_id(co_b_row_id, co_a)`` must return ``None`` — not the
  row by id alone — so a guessed/leaked id can't bypass scoping.
* ``exists_post_ledger_blocked(co_b_doc_id, co_a)`` must be ``False``.
* ``mark_resolved`` / ``mark_dismissed`` cross-tenant return ``None``
  and don't mutate the other tenant's row.

The test uses a real Postgres connection so any SQL-layer
permissiveness (e.g. a missed WHERE) surfaces as a real failure.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest

from app.core.db import AsyncSessionLocal
from app.modules.healthcheck.models import Company, HealthCheckResult
from app.modules.healthcheck.repository import HealthCheckResultRepository


# pytest-asyncio is configured with ``asyncio_mode = auto`` in pytest.ini,
# so every coroutine test is awaited automatically; no per-test marker
# needed.


# ----------------------- async fixtures -----------------------------

@pytest.fixture
async def two_tenants() -> AsyncIterator[dict[str, object]]:
    """Insert Co A + Co B, each with one ``post_ledger`` / ``blocked``
    row. Cleaned up at teardown so the test doesn't pollute the DB.
    """
    co_a_id = uuid.uuid4()
    co_b_id = uuid.uuid4()
    co_a_doc_id = uuid.uuid4()
    co_b_doc_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(Company(id=co_a_id, name="Co A (multi-tenant test)"))
            db.add(Company(id=co_b_id, name="Co B (multi-tenant test)"))
            co_a_row = HealthCheckResult(
                company_id=co_a_id,
                document_id=co_a_doc_id,
                document_type="ACCPAY",
                kind="post_ledger",
                status="blocked",
                result={"flagged": [{"rule_id": "missing_invoice_number"}]},
            )
            co_b_row = HealthCheckResult(
                company_id=co_b_id,
                document_id=co_b_doc_id,
                document_type="ACCPAY",
                kind="post_ledger",
                status="blocked",
                result={"flagged": [{"rule_id": "duplicate_bill"}]},
            )
            db.add(co_a_row)
            db.add(co_b_row)
            await db.flush()
            co_a_row_id = co_a_row.id
            co_b_row_id = co_b_row.id

    yield {
        "co_a_id": co_a_id,
        "co_b_id": co_b_id,
        "co_a_doc_id": co_a_doc_id,
        "co_b_doc_id": co_b_doc_id,
        "co_a_row_id": co_a_row_id,
        "co_b_row_id": co_b_row_id,
    }

    # Teardown — CASCADE on company_id wipes the rows.
    async with AsyncSessionLocal() as db:
        async with db.begin():
            for cid in (co_a_id, co_b_id):
                company = await db.get(Company, cid)
                if company is not None:
                    await db.delete(company)


# --------------------------- tests ----------------------------------

async def test_list_does_not_leak_across_tenants(two_tenants):
    async with AsyncSessionLocal() as db:
        repo = HealthCheckResultRepository(db)
        co_a_rows = await repo.list_post_ledger_trapped(two_tenants["co_a_id"])
        co_b_rows = await repo.list_post_ledger_trapped(two_tenants["co_b_id"])

    assert {r.id for r in co_a_rows} == {two_tenants["co_a_row_id"]}
    assert {r.id for r in co_b_rows} == {two_tenants["co_b_row_id"]}


async def test_find_by_id_cross_tenant_returns_none(two_tenants):
    async with AsyncSessionLocal() as db:
        repo = HealthCheckResultRepository(db)
        # Co A asking for Co B's row id MUST return None.
        leak = await repo.find_by_id(
            two_tenants["co_b_row_id"],
            two_tenants["co_a_id"],
        )
        own = await repo.find_by_id(
            two_tenants["co_a_row_id"],
            two_tenants["co_a_id"],
        )
    assert leak is None
    assert own is not None and own.id == two_tenants["co_a_row_id"]


async def test_exists_post_ledger_blocked_is_scoped(two_tenants):
    async with AsyncSessionLocal() as db:
        repo = HealthCheckResultRepository(db)
        own = await repo.exists_post_ledger_blocked(
            two_tenants["co_a_doc_id"], two_tenants["co_a_id"],
        )
        leak = await repo.exists_post_ledger_blocked(
            two_tenants["co_b_doc_id"], two_tenants["co_a_id"],
        )
    assert own is True
    assert leak is False


async def test_mark_resolved_cross_tenant_noop(two_tenants):
    async with AsyncSessionLocal() as db:
        async with db.begin():
            repo = HealthCheckResultRepository(db)
            result = await repo.mark_resolved(
                two_tenants["co_b_row_id"],
                two_tenants["co_a_id"],
                resolution_notes="should not happen",
            )
        assert result is None

    # Confirm Co B's row was not mutated.
    async with AsyncSessionLocal() as db:
        row = await db.get(HealthCheckResult, two_tenants["co_b_row_id"])
        assert row is not None
        assert "resolved" not in (row.result or {})
        assert "resolution_notes" not in (row.result or {})
