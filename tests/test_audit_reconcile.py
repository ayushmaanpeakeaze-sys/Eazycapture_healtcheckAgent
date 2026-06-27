"""Re-run reconciliation: 'latest run wins'.

When the audit re-runs, a document that is no longer flagged should be
auto-cleared (dropped from the actionable feed, kept in the DB), while still-
flagged rows keep their first score and explicit user decisions stay sticky.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import Company, HealthCheckResult
from app.modules.healthcheck.tasks import _auto_clear_stale

_FEED = "/api/v1/health/trapped-invoices/"


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _insert_company(name: str) -> uuid.UUID:
    cid = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(Company(id=cid, name=name, is_active=True))
        db.commit()
    return cid


def _delete_company(cid: uuid.UUID) -> None:
    with SyncSessionLocal() as db:
        c = db.get(Company, cid)
        if c is not None:
            db.delete(c)
            db.commit()


def _insert_row(company_id: uuid.UUID, document_id: uuid.UUID, result: dict) -> uuid.UUID:
    row_id = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=row_id, company_id=company_id, document_id=document_id,
            document_type="ACCREC", kind="post_ledger", status="blocked",
            error_msgs="x", result=result,
        ))
        db.commit()
    return row_id


def _result_of(row_id: uuid.UUID) -> dict:
    with SyncSessionLocal() as db:
        return dict(db.get(HealthCheckResult, row_id).result or {})


def test_auto_clear_marks_stale_only_and_preserves_user_actions():
    co = _insert_company("Reconcile unit test")
    try:
        doc_a, doc_b, doc_c, doc_d = (uuid.uuid4() for _ in range(4))
        row_a = _insert_row(co, doc_a, {"flagged": [{"rule_id": "duplicate_invoice"}]})            # still flagged
        row_b = _insert_row(co, doc_b, {"flagged": [{"rule_id": "duplicate_invoice"}]})            # stale + open
        row_c = _insert_row(co, doc_c, {"flagged": [{"rule_id": "duplicate_invoice"}], "resolved": True})   # stale but resolved
        row_d = _insert_row(co, doc_d, {"flagged": [{"rule_id": "duplicate_invoice"}], "dismissed": True})  # stale but dismissed

        # This run evaluated {A,B,C,D} and only flagged A → stale = {B,C,D}.
        with SyncSessionLocal() as db:
            cleared = _auto_clear_stale(
                db, company_id=co, batch_id="batch-2", stale_doc_ids={doc_b, doc_c, doc_d},
            )
            db.commit()

        assert cleared == 1                                   # only the open stale row B
        assert _result_of(row_b).get("auto_cleared") is True
        assert _result_of(row_b).get("auto_cleared_batch_id") == "batch-2"
        assert "auto_cleared" not in _result_of(row_a)        # still flagged → untouched
        assert "auto_cleared" not in _result_of(row_c)        # resolved → preserved
        assert "auto_cleared" not in _result_of(row_d)        # dismissed → preserved
    finally:
        _delete_company(co)


async def test_auto_cleared_row_drops_out_of_feed(async_client):
    co = _insert_company("Reconcile feed test")
    try:
        keep = _insert_row(co, uuid.uuid4(), {"flagged": [{"rule_id": "duplicate_invoice"}]})
        gone = _insert_row(co, uuid.uuid4(), {"flagged": [{"rule_id": "duplicate_invoice"}]})

        async def total():
            r = await async_client.get(f"{_FEED}?company_id={co}")
            assert r.status_code == 200, r.text
            return r.json()["total"]

        assert await total() == 2

        # Mark `gone` stale (no longer flagged this run).
        with SyncSessionLocal() as db:
            gone_doc = db.get(HealthCheckResult, gone).document_id
            _auto_clear_stale(db, company_id=co, batch_id="b2", stale_doc_ids={gone_doc})
            db.commit()

        # Only the still-flagged row remains in the feed.
        assert await total() == 1
        _ = keep
    finally:
        _delete_company(co)
