"""Companies panorama tests.

Builds two scratch companies + matching ``audit_batch`` and
``health_check_result`` rows, then asserts:

1. Every active company shows up in the panorama.
2. The ``health_score`` formula is correct
   (``round(100 * (audited - trapped) / audited)``), clamped 0..100.
3. ``health_score`` is ``None`` for companies that were never audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import (
    AuditBatch,
    Company,
    HealthCheckResult,
)


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


# ---------- DB helpers ---------------------------------------------

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


def _insert_completed_audit(
    company_id: uuid.UUID,
    *,
    total: int,
    trapped: int,
) -> None:
    with SyncSessionLocal() as db:
        db.add(AuditBatch(
            id=uuid.uuid4(),
            company_id=company_id,
            status="completed",
            total=total,
            trapped=trapped,
            new_trapped=trapped,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        ))
        db.commit()


def _insert_trapped(
    company_id: uuid.UUID,
    rule_id: str,
) -> None:
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=uuid.uuid4(),
            company_id=company_id,
            document_id=uuid.uuid4(),
            document_type="ACCPAY",
            kind="post_ledger",
            status="blocked",
            error_msgs=f"{rule_id} fixture message",
            result={
                "flagged": [{"rule_id": rule_id, "issue_type": rule_id}],
                "rule_ids": [rule_id],
                "messages": f"{rule_id} fixture message",
            },
        ))
        db.commit()


def _pick(rows: list[dict], company_id: uuid.UUID) -> dict | None:
    for row in rows:
        if row["company_id"] == str(company_id):
            return row
    return None


# =====================================================================
# Tests
# =====================================================================

async def test_panorama_returns_row_per_company(async_client: httpx.AsyncClient):
    co_audited = _insert_company("Panorama — audited")
    co_never = _insert_company("Panorama — never audited")
    try:
        # Audited company: 10 total, 4 trapped → score = round(100 * 6/10) = 60.
        _insert_completed_audit(co_audited, total=10, trapped=4)
        for rule in ("duplicate_bill", "duplicate_bill",
                     "missing_invoice_number", "future_dated"):
            _insert_trapped(co_audited, rule)

        resp = await async_client.get("/api/v1/health/companies-panorama/")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        row_a = _pick(body["results"], co_audited)
        row_n = _pick(body["results"], co_never)
        assert row_a is not None
        assert row_n is not None

        # Audited row matches the expected score + most-common rule_id.
        assert row_a["post_audited_total"] == 10
        assert row_a["trapped_count"] == 4
        assert row_a["health_score"] == 60
        assert row_a["top_issue"] == "duplicate_bill"  # 2× vs 1× each
        # Never-audited row uses None for score so the UI can show a
        # "no data yet" state instead of an alarming red zero.
        assert row_n["post_audited_total"] == 0
        assert row_n["trapped_count"] == 0
        assert row_n["health_score"] is None

        assert body["window_days"] == 30
    finally:
        _delete_company(co_audited)
        _delete_company(co_never)


async def test_panorama_score_formula_clamped(async_client: httpx.AsyncClient):
    """trapped > audited would push the formula negative; the service
    must clamp to 0 so the UI never sees a nonsense value."""
    co = _insert_company("Panorama — over-trapped clamp")
    try:
        # 5 audited, 8 trapped (a re-audit-mismatch state) → raw score -60.
        _insert_completed_audit(co, total=5, trapped=8)
        for _ in range(8):
            _insert_trapped(co, "future_dated")

        resp = await async_client.get("/api/v1/health/companies-panorama/")
        assert resp.status_code == 200
        row = _pick(resp.json()["results"], co)
        assert row is not None
        assert row["health_score"] == 0
    finally:
        _delete_company(co)
