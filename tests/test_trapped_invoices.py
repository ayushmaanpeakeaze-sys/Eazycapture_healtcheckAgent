"""Trapped-invoices feed tests.

Each test creates its own scratch company so the fixtures don't fight
the live Demo Co data from the audit dispatch test. Uses
``SyncSessionLocal`` for direct row inserts to dodge the async-engine
event-loop trap (see ``test_audit_dispatch.py``).
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
import redis as sync_redis

from app.core.config import settings
from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import Company, HealthCheckResult


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


# ---------- DB / Redis helpers ----------

def _insert_company(name: str) -> uuid.UUID:
    cid = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(Company(id=cid, name=name, is_active=True))
        db.commit()
    return cid


def _delete_company(cid: uuid.UUID) -> None:
    with SyncSessionLocal() as db:
        company = db.get(Company, cid)
        if company is not None:
            db.delete(company)
            db.commit()


def _insert_trapped(
    company_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    document_type: str = "ACCPAY",
    result: dict | None = None,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=row_id,
            company_id=company_id,
            document_id=document_id,
            document_type=document_type,
            kind="post_ledger",
            status="blocked",
            error_msgs="fixture flag",
            result=result or {"flagged": [{"rule_id": "fixture_rule"}]},
        ))
        db.commit()
    return row_id


# =====================================================================
# Tests
# =====================================================================

async def test_list_returns_only_active(async_client: httpx.AsyncClient):
    """Resolved + dismissed rows must be excluded by the JSONB filter."""
    co = _insert_company("Trapped Test Co (active filter)")
    try:
        doc_active = uuid.uuid4()
        doc_resolved = uuid.uuid4()
        doc_dismissed = uuid.uuid4()
        _insert_trapped(co, document_id=doc_active)
        _insert_trapped(
            co, document_id=doc_resolved,
            result={"flagged": [], "resolved": True},
        )
        _insert_trapped(
            co, document_id=doc_dismissed,
            result={"flagged": [], "dismissed": True},
        )

        resp = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co}",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert len(body["results"]) == 1
        assert body["results"][0]["document_id"] == str(doc_active)
        # xero_url must be set (ACCPAY → AccountsPayable/Bills view).
        assert "AccountsPayable" in body["results"][0]["xero_url"]
        assert body["results"][0]["xero_url"].endswith(f"InvoiceID={doc_active}")
        assert body["results"][0]["ai"] is None
    finally:
        _delete_company(co)


async def test_ai_annotation_spliced_from_redis(
    async_client: httpx.AsyncClient,
):
    """When a Redis ``health_check_ai:{doc_id}`` record exists, the
    feed must splice it into the row's ``ai`` field."""
    co = _insert_company("Trapped Test Co (AI splice)")
    doc_id = uuid.uuid4()
    _insert_trapped(co, document_id=doc_id, document_type="ACCREC")

    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    key = f"health_check_ai:{doc_id}"
    try:
        r.set(key, json.dumps({
            "explanation": "Customer overdue 120 days; cash-flow risk.",
            "severity_ai": "high",
            "confidence": 0.95,
            "regulatory_ref": "HMRC Record Keeping Guidance",
        }))

        resp = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co}",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        ai = body["results"][0]["ai"]
        assert ai is not None
        assert ai["severity_ai"] == "high"
        assert ai["confidence"] == 0.95
        assert "Customer overdue" in ai["explanation"]
        # ACCREC → AccountsReceivable (sales) classic view URL.
        assert body["results"][0]["xero_url"].startswith(
            "https://go.xero.com/AccountsReceivable/View.aspx"
        )
    finally:
        r.delete(key)
        r.close()
        _delete_company(co)


async def test_cross_tenant_isolation(async_client: httpx.AsyncClient):
    """A query against Co A must not see Co B's trapped rows."""
    co_a = _insert_company("Tenant A (trapped isolation)")
    co_b = _insert_company("Tenant B (trapped isolation)")
    try:
        doc_a = uuid.uuid4()
        doc_b = uuid.uuid4()
        _insert_trapped(co_a, document_id=doc_a)
        _insert_trapped(co_b, document_id=doc_b)

        resp_a = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co_a}",
        )
        resp_b = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co_b}",
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["total"] == 1
        assert resp_b.json()["total"] == 1
        assert resp_a.json()["results"][0]["document_id"] == str(doc_a)
        assert resp_b.json()["results"][0]["document_id"] == str(doc_b)
        # The cross-tenant ids never appear in each other's response.
        assert resp_a.json()["results"][0]["document_id"] != str(doc_b)
    finally:
        _delete_company(co_a)
        _delete_company(co_b)
