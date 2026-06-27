"""Day 7 — per-company health summary test.

Asserts that ``top_issues`` groups trapped rows by their primary
rule id, returns the most common ones first, and exposes a
``sample_msg`` for each.
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


def _insert_completed_audit(cid: uuid.UUID, *, total: int, trapped: int) -> None:
    with SyncSessionLocal() as db:
        db.add(AuditBatch(
            id=uuid.uuid4(),
            company_id=cid,
            status="completed",
            total=total,
            trapped=trapped,
            new_trapped=trapped,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        ))
        db.commit()


def _insert_trapped(
    cid: uuid.UUID, rule_id: str, message: str,
) -> None:
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=uuid.uuid4(),
            company_id=cid,
            document_id=uuid.uuid4(),
            document_type="ACCPAY",
            kind="post_ledger",
            status="blocked",
            error_msgs=message,
            result={
                "flagged": [{"rule_id": rule_id, "issue_type": rule_id,
                             "message": message}],
                "rule_ids": [rule_id],
                "messages": message,
            },
        ))
        db.commit()


async def test_summary_top_issues_groups_and_orders(
    async_client: httpx.AsyncClient,
):
    co = _insert_company("Summary fixture")
    try:
        _insert_completed_audit(co, total=20, trapped=5)

        # 3× duplicate, 1× missing_invoice_number, 1× future_dated.
        _insert_trapped(
            co, "duplicate_bill",
            "Hamilton Smith — likely duplicate of INV-0001",
        )
        _insert_trapped(
            co, "duplicate_bill",
            "Hamilton Smith — likely duplicate, sibling row",
        )
        _insert_trapped(
            co, "duplicate_bill",
            "Another Vendor — duplicate suspected",
        )
        _insert_trapped(
            co, "missing_invoice_number",
            "Gateway Motors — invoice number missing",
        )
        _insert_trapped(
            co, "future_dated",
            "FUT-Vendor — invoice dated 2027-01-01",
        )

        resp = await async_client.get(
            f"/api/v1/health/summary/?company_id={co}",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["company_id"] == str(co)
        assert body["post_audited_total"] == 20
        assert body["trapped_count"] == 5
        # 20 audited, 5 trapped → score = round(100 * 15/20) = 75.
        assert body["health_score"] == 75

        issues = body["top_issues"]
        assert len(issues) >= 3
        # Most common rule appears first.
        assert issues[0]["issue_type"] == "duplicate_bill"
        assert issues[0]["count"] == 3
        # Sample message should be populated for each issue type.
        assert "Hamilton Smith" in (issues[0]["sample_msg"] or "")
        # Remaining issues round out the list.
        rule_counts = {i["issue_type"]: i["count"] for i in issues}
        assert rule_counts["missing_invoice_number"] == 1
        assert rule_counts["future_dated"] == 1
    finally:
        _delete_company(co)
