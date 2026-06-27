"""Day 5 — resolve / dismiss / apply-ai-fix tests.

Each test creates its own scratch company + rows so the live Demo Co
data and the other test modules don't interfere. Tests pass the AI
suggestion as an override so the rules engine doesn't have to be
running.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

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


# ---------- DB helpers (sync) ----------

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
    document_id: uuid.UUID | None = None,
    document_type: str = "ACCPAY",
    rule_id: str = "duplicate_bill",
    extra_result: dict | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    row_id = uuid.uuid4()
    doc_id = document_id or uuid.uuid4()
    result = {
        "flagged": [{"rule_id": rule_id, "issue_type": rule_id,
                     "message": "fixture flag"}],
        "rule_ids": [rule_id],
        "messages": "fixture flag",
    }
    if extra_result:
        result.update(extra_result)
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=row_id,
            company_id=company_id,
            document_id=doc_id,
            document_type=document_type,
            kind="post_ledger",
            status="blocked",
            error_msgs="fixture flag",
            result=result,
        ))
        db.commit()
    return row_id, doc_id


def _read_row(row_id: uuid.UUID) -> HealthCheckResult:
    with SyncSessionLocal() as db:
        return db.execute(
            select(HealthCheckResult).where(HealthCheckResult.id == row_id)
        ).scalar_one()


# =====================================================================
# 1. resolve()
# =====================================================================

async def test_resolve_marks_row_resolved(async_client: httpx.AsyncClient):
    co = _insert_company("Resolve test")
    try:
        row_id, doc_id = _insert_trapped(co)

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row_id}/resolve/?company_id={co}",
            json={"field_updates": {"Status": "VOIDED"},
                  "resolution_notes": "manual void"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        assert body["applied_updates"] == {"Status": "VOIDED"}
        assert body["xero_response"]["stub"] is True
        assert body["xero_response"]["would_apply"]["Status"] == "VOIDED"
        assert body["xero_url"]  # ACCPAY → AccountsPayable URL

        # Row should now drop out of the trapped feed.
        feed = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co}",
        )
        assert feed.json()["total"] == 0

        row = _read_row(row_id)
        assert row.result.get("resolved") is True
        assert row.result.get("resolution_notes") == "manual void"
    finally:
        _delete_company(co)


# =====================================================================
# 2. dismiss()
# =====================================================================

async def test_dismiss_marks_row_dismissed(async_client: httpx.AsyncClient):
    co = _insert_company("Dismiss test")
    try:
        row_id, _ = _insert_trapped(co)

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row_id}/dismiss/?company_id={co}",
            json={"dismissal_reason": "false positive"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dismissed"] is True

        # Row should drop out of the trapped feed.
        feed = await async_client.get(
            f"/api/v1/health/trapped-invoices/?company_id={co}",
        )
        assert feed.json()["total"] == 0

        row = _read_row(row_id)
        assert row.result.get("dismissed") is True
        assert row.result.get("dismissal_reason") == "false positive"
    finally:
        _delete_company(co)


# =====================================================================
# 3. apply-ai-fix() — placeholder rejection
# =====================================================================

async def test_apply_ai_fix_placeholder_rejected(
    async_client: httpx.AsyncClient,
):
    co = _insert_company("Placeholder test")
    try:
        row_id, _ = _insert_trapped(co, rule_id="missing_invoice_number")

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row_id}/apply-ai-fix/?company_id={co}",
            json={
                "suggestion": {
                    "fix_strategy": "add_invoice_number",
                    "field_updates": {"InvoiceNumber": "FIXME-001"},
                },
            },
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["resolved"] is False
        assert body["error_code"] == "MANUAL_FIX_REQUIRED"
        assert body["xero_url"]  # must be present so frontend can deep-link
        assert "FIXME" in body["error_detail"]

        # Row should NOT be marked resolved.
        row = _read_row(row_id)
        assert row.result.get("resolved") is not True
    finally:
        _delete_company(co)


# =====================================================================
# 4. apply-ai-fix() — duplicate-target redirect
# =====================================================================

async def test_apply_ai_fix_redirects_for_duplicate_target(
    async_client: httpx.AsyncClient,
):
    """User clicks INV-0001's trapped row. AI's suggestion targets
    INV-0005 (the duplicate to void). The apply service must resolve
    INV-0005's row, not INV-0001's."""
    co = _insert_company("Duplicate redirect test")
    try:
        row_a, doc_a = _insert_trapped(  # "INV-0001"
            co, document_type="ACCREC", rule_id="duplicate_bill",
        )
        row_b, doc_b = _insert_trapped(  # "INV-0005" — the one to void
            co, document_type="ACCREC", rule_id="duplicate_bill",
        )

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row_a}/apply-ai-fix/?company_id={co}",
            json={
                "suggestion": {
                    "fix_strategy": "void_duplicate_invoice",
                    "field_updates": {"Status": "VOIDED"},
                    "target_transaction_id": str(doc_b),
                },
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        # ← the redirect: row_id and document_id must reference the
        # sibling, not the row the user clicked.
        assert body["row_id"] == str(row_b)
        assert body["document_id"] == str(doc_b)
        assert body["ai_applied"] is True
        assert body["ai_fix_strategy"] == "void_duplicate_invoice"

        # Sibling row resolved, original still trapped.
        sibling = _read_row(row_b)
        original = _read_row(row_a)
        assert sibling.result.get("resolved") is True
        assert original.result.get("resolved") is not True
    finally:
        _delete_company(co)
