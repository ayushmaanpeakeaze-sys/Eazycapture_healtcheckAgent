"""Local-state action buttons: snooze / mark-OK / bulk.

These are the 'har ek button' actions that need no Xero write + no sir sign-off.
They write flags into the row's ``result`` JSONB and drop the row from the
actionable feed (snoozed rows reappear once the window passes).
"""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import Company, HealthCheckResult

_FEED = "/api/v1/health/trapped-invoices/"


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


def _insert_trapped(company_id: uuid.UUID, *, result: dict | None = None) -> uuid.UUID:
    row_id = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=row_id, company_id=company_id, document_id=uuid.uuid4(),
            document_type="ACCPAY", kind="post_ledger", status="blocked",
            error_msgs="fixture flag",
            result=result or {"flagged": [{"rule_id": "fixture_rule"}]},
        ))
        db.commit()
    return row_id


def _read_result(row_id: uuid.UUID) -> dict:
    with SyncSessionLocal() as db:
        return dict(db.get(HealthCheckResult, row_id).result or {})


async def _feed_total(ac, co) -> int:
    resp = await ac.get(f"{_FEED}?company_id={co}")
    assert resp.status_code == 200, resp.text
    return resp.json()["total"]


# ---------------------------------------------------------------- snooze

async def test_snooze_hides_row_and_sets_expiry(async_client):
    co = _insert_company("Snooze test")
    try:
        row = _insert_trapped(co)
        assert await _feed_total(async_client, co) == 1

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/snooze/?company_id={co}",
            json={"days": 30, "reason": "review next month"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["snoozed"] is True
        assert body["snoozed_until"]  # ISO string

        # dropped from the feed
        assert await _feed_total(async_client, co) == 0
        # persisted an epoch expiry roughly 30 days out
        res = _read_result(row)
        target = (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
        assert abs(res["snoozed_until_ts"] - target) < 120
    finally:
        _delete_company(co)


async def test_expired_snooze_reappears(async_client):
    co = _insert_company("Snooze expired test")
    try:
        # snoozed_until_ts in the PAST → should still be in the feed.
        past = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "x"}], "snoozed_until_ts": past,
        })
        assert await _feed_total(async_client, co) == 1
        _ = row
    finally:
        _delete_company(co)


# --------------------------------------------------------------- mark-ok

async def test_mark_ok_hides_row(async_client):
    co = _insert_company("Mark-OK test")
    try:
        row = _insert_trapped(co)
        assert await _feed_total(async_client, co) == 1

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/mark-ok/?company_id={co}",
            json={"reason": "legit prepayment"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["marked_ok"] is True
        assert await _feed_total(async_client, co) == 0
        assert _read_result(row)["marked_ok"] is True
        assert _read_result(row)["mark_ok_reason"] == "legit prepayment"
    finally:
        _delete_company(co)


# --------------------------------------------------------------- restore

async def test_restore_brings_marked_ok_row_back(async_client):
    co = _insert_company("Restore mark-ok test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "x"}], "marked_ok": True,
            "mark_ok_reason": "was accepted",
        })
        assert await _feed_total(async_client, co) == 0       # hidden
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/restore/?company_id={co}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["restored"] is True
        assert await _feed_total(async_client, co) == 1       # back on the list
        res = _read_result(row)
        assert "marked_ok" not in res and "mark_ok_reason" not in res
    finally:
        _delete_company(co)


async def test_restore_brings_dismissed_row_back(async_client):
    co = _insert_company("Restore dismiss test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "x"}], "dismissed": True, "dismissal_reason": "oops",
        })
        assert await _feed_total(async_client, co) == 0
        await async_client.post(f"/api/v1/health/trapped/{row}/restore/?company_id={co}")
        assert await _feed_total(async_client, co) == 1
        res = _read_result(row)
        assert "dismissed" not in res and "dismissal_reason" not in res
    finally:
        _delete_company(co)


async def test_restore_leaves_resolved_hidden(async_client):
    # A genuinely fixed (resolved) row must stay hidden — restore only clears the
    # USER hide-flags, never the resolved state.
    co = _insert_company("Restore resolved test")
    try:
        row = _insert_trapped(co, result={"flagged": [{"rule_id": "x"}], "resolved": True})
        assert await _feed_total(async_client, co) == 0
        await async_client.post(f"/api/v1/health/trapped/{row}/restore/?company_id={co}")
        assert _read_result(row)["resolved"] is True          # untouched
        assert await _feed_total(async_client, co) == 0       # still hidden
    finally:
        _delete_company(co)


async def test_bulk_restore_applies_to_many(async_client):
    co = _insert_company("Bulk restore test")
    try:
        rows = [str(_insert_trapped(co, result={"flagged": [{"rule_id": "x"}], "marked_ok": True}))
                for _ in range(3)]
        assert await _feed_total(async_client, co) == 0
        resp = await async_client.post(
            f"/api/v1/health/trapped/bulk/?company_id={co}",
            json={"row_ids": rows, "action": "restore"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["succeeded"] == 3
        assert await _feed_total(async_client, co) == 3
    finally:
        _delete_company(co)


# ----------------------------------------------------------------- bulk

async def test_bulk_dismiss_applies_to_many(async_client):
    co = _insert_company("Bulk test")
    try:
        rows = [str(_insert_trapped(co)) for _ in range(3)]
        assert await _feed_total(async_client, co) == 3

        resp = await async_client.post(
            f"/api/v1/health/trapped/bulk/?company_id={co}",
            json={"row_ids": rows, "action": "dismiss", "reason": "batch cleanup"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requested"] == 3
        assert body["succeeded"] == 3
        assert body["failed"] == 0
        assert await _feed_total(async_client, co) == 0
    finally:
        _delete_company(co)


async def test_show_dismissed_matches_toggle(async_client):
    co = _insert_company("Show-dismissed test")
    try:
        row = _insert_trapped(co)
        assert await _feed_total(async_client, co) == 1
        await async_client.post(f"/api/v1/health/trapped/{row}/dismiss/?company_id={co}", json={})
        # default feed hides it
        assert await _feed_total(async_client, co) == 0
        # show-dismissed reveals it
        resp = await async_client.get(f"{_FEED}?company_id={co}&include_dismissed=true")
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
    finally:
        _delete_company(co)


async def test_show_marked_ok_toggle(async_client):
    # The supplier checks use "Mark as OK" + a "Show items marked as OK" toggle —
    # that toggle maps to include_marked_ok (NOT include_dismissed).
    co = _insert_company("Show-marked-ok test")
    try:
        row = _insert_trapped(co)
        assert await _feed_total(async_client, co) == 1
        await async_client.post(f"/api/v1/health/trapped/{row}/mark-ok/?company_id={co}", json={})
        assert await _feed_total(async_client, co) == 0                          # hidden by default
        # include_marked_ok reveals it
        resp = await async_client.get(f"{_FEED}?company_id={co}&include_marked_ok=true")
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        # include_dismissed alone must NOT reveal a marked-OK row (distinct states)
        resp2 = await async_client.get(f"{_FEED}?company_id={co}&include_dismissed=true")
        assert resp2.json()["total"] == 0
    finally:
        _delete_company(co)


async def test_issue_type_filter_and_total_value(async_client):
    # Per-check page: scope by issue_type + get the £ "Total Potential Errors"
    # (sum of amount_due across ALL matching rows, not just the page).
    co = _insert_company("issue-type filter test")
    try:
        _insert_trapped(co, result={
            "flagged": [{"issue_type": "old_unsettled_sales_credit"}], "amount_due": "400.00"})
        _insert_trapped(co, result={
            "flagged": [{"issue_type": "old_unsettled_sales_credit"}], "amount_due": "600.00"})
        _insert_trapped(co, result={
            "flagged": [{"issue_type": "duplicate_invoice"}], "amount_due": "999.00"})

        # scoped to the credit check → 2 rows, total_value 1000
        resp = await async_client.get(
            f"{_FEED}?company_id={co}&issue_type=old_unsettled_sales_credit")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert float(body["total_value"]) == 1000.0
        # no filter → all three rows
        allresp = await async_client.get(f"{_FEED}?company_id={co}")
        assert allresp.json()["total"] == 3
    finally:
        _delete_company(co)


async def test_exclude_bank_items_toggle(async_client):
    # "Show Bank payments too" OFF (exclude_bank_items=true) hides Money In/Out.
    co = _insert_company("bank-toggle test")
    try:
        with SyncSessionLocal() as db:
            for dt in ("ACCPAY", "SPEND"):
                db.add(HealthCheckResult(
                    id=uuid.uuid4(), company_id=co, document_id=uuid.uuid4(),
                    document_type=dt, kind="post_ledger", status="blocked",
                    error_msgs="x",
                    result={"flagged": [{"issue_type": "sales_tax_on_bills"}]}))
            db.commit()
        # default → both visible
        resp = await async_client.get(f"{_FEED}?company_id={co}")
        assert resp.json()["total"] == 2
        # toggle OFF → only the bill (SPEND hidden)
        resp2 = await async_client.get(f"{_FEED}?company_id={co}&exclude_bank_items=true")
        assert resp2.json()["total"] == 1
    finally:
        _delete_company(co)


async def test_recheck_attachment_stub_when_not_connected(async_client):
    co = _insert_company("recheck stub")
    try:
        row = _insert_trapped(co, result={"flagged": [{"issue_type": "undocumented_bill"}]})
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/recheck-attachment/?company_id={co}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stub"] is True and body["attached"] is False
    finally:
        _delete_company(co)


async def test_recheck_resolves_when_attachment_found(async_client, monkeypatch):
    from app.modules.integrations.service import IntegrationService
    monkeypatch.setattr(IntegrationService, "is_connected", lambda self, c, t: True)

    async def _fake_fetch(self, c, t, dt, did):
        return {"Invoices": [{"HasAttachments": True}]}
    monkeypatch.setattr(IntegrationService, "fetch_attachable", _fake_fetch)

    co = _insert_company("recheck resolves")
    try:
        row = _insert_trapped(co, result={"flagged": [{"issue_type": "undocumented_bill"}]})
        assert await _feed_total(async_client, co) == 1
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/recheck-attachment/?company_id={co}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["attached"] is True and resp.json()["resolved"] is True
        assert await _feed_total(async_client, co) == 0      # dropped — now documented
    finally:
        _delete_company(co)


async def test_upload_attachment_resolves(async_client, monkeypatch):
    from app.modules.integrations.service import IntegrationService
    monkeypatch.setattr(IntegrationService, "is_connected", lambda self, c, t: True)

    async def _fake_upload(self, c, t, dt, did, fn, content, ct):
        assert content == b"%PDF-1.4 fake"
        return {"Attachments": [{"FileName": fn}]}
    monkeypatch.setattr(IntegrationService, "upload_attachment", _fake_upload)

    co = _insert_company("upload resolves")
    try:
        row = _insert_trapped(co, result={"flagged": [{"issue_type": "undocumented_bill"}]})
        assert await _feed_total(async_client, co) == 1
        b64 = base64.b64encode(b"%PDF-1.4 fake").decode()
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/attachment/?company_id={co}",
            json={"filename": "invoice.pdf", "content_type": "application/pdf",
                  "content_base64": b64})
        assert resp.status_code == 200, resp.text
        assert resp.json()["uploaded"] is True and resp.json()["resolved"] is True
        assert await _feed_total(async_client, co) == 0
    finally:
        _delete_company(co)


async def test_void_unpaid_resolves(async_client):
    co = _insert_company("Void unpaid test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "duplicate_invoice"}],
            "invoice_status": "AUTHORISED", "amount_paid": "0",
        })
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/void/?company_id={co}",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        assert body["applied_updates"]["Status"] == "VOIDED"
    finally:
        _delete_company(co)


async def test_void_paid_blocked(async_client):
    co = _insert_company("Void paid test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "duplicate_invoice"}],
            "invoice_status": "PAID", "amount_paid": "541.25",
        })
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/void/?company_id={co}",
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["resolved"] is False
        assert body["error_code"] == "HAS_PAYMENT_OR_CREDIT"
    finally:
        _delete_company(co)


async def test_credit_note_resolves_via_stub(async_client):
    co = _insert_company("Credit note test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "old_unpaid_invoice"}],
            "invoice_status": "AUTHORISED", "amount_due": "541.25",
        })
        assert await _feed_total(async_client, co) == 1

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/credit-note/?company_id={co}",
            json={"reason": "agreed write-off"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        # No live Nango in tests → stub path, but the action is recorded.
        assert body["xero_response"]["stub"] is True
        assert body["xero_response"]["action"] == "credit_note_created"
        # row resolved → drops out of the feed, with the reason persisted.
        assert await _feed_total(async_client, co) == 0
        assert _read_result(row)["resolution_notes"] == "agreed write-off"
    finally:
        _delete_company(co)


async def test_credit_note_already_resolved_blocked(async_client):
    co = _insert_company("Credit note dup test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "old_unpaid_invoice"}], "resolved": True,
        })
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/credit-note/?company_id={co}",
            json={},
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["resolved"] is False
        assert body["error_code"] == "ALREADY_RESOLVED"
    finally:
        _delete_company(co)


async def test_credit_note_not_allocated_keeps_bill(async_client, monkeypatch):
    # Real Xero credit note CREATED but NOT allocated → AmountDue unchanged →
    # the bill must STAY in Old Unpaid (Scenario 1), not vanish.
    co = _insert_company("CN alloc-fail test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "old_unpaid_invoice"}],
            "invoice_status": "AUTHORISED", "amount_due": "541.25",
        })
        assert await _feed_total(async_client, co) == 1

        async def _fake(self, **kw):
            return {"stub": False, "action": "credit_note_created",
                    "xero_response": {"CreditNotes": [{"CreditNoteID": "cn-1"}],
                                      "allocation": None}}   # allocation FAILED
        monkeypatch.setattr(
            "app.modules.healthcheck.services.resolve_service.ResolveService._call_xero_credit_note",
            _fake,
        )
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/credit-note/?company_id={co}", json={},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "CREDIT_NOTE_NOT_ALLOCATED"
        assert await _feed_total(async_client, co) == 1   # bill STILL there
    finally:
        _delete_company(co)


async def test_credit_note_allocated_clears_bill(async_client, monkeypatch):
    # Real Xero credit note created AND allocated → AmountDue → 0 → bill clears.
    co = _insert_company("CN alloc-ok test")
    try:
        row = _insert_trapped(co, result={
            "flagged": [{"rule_id": "old_unpaid_invoice"}],
            "invoice_status": "AUTHORISED", "amount_due": "541.25",
        })
        async def _fake(self, **kw):
            return {"stub": False, "action": "credit_note_created",
                    "xero_response": {"CreditNotes": [{"CreditNoteID": "cn-1"}],
                                      "allocation": {"Amount": 541.25}}}  # allocated
        monkeypatch.setattr(
            "app.modules.healthcheck.services.resolve_service.ResolveService._call_xero_credit_note",
            _fake,
        )
        resp = await async_client.post(
            f"/api/v1/health/trapped/{row}/credit-note/?company_id={co}", json={},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["resolved"] is True
        assert await _feed_total(async_client, co) == 0   # bill cleared
    finally:
        _delete_company(co)


async def test_bulk_reports_per_row_failure(async_client):
    co = _insert_company("Bulk partial test")
    try:
        good = str(_insert_trapped(co))
        missing = str(uuid.uuid4())  # not in this company

        resp = await async_client.post(
            f"/api/v1/health/trapped/bulk/?company_id={co}",
            json={"row_ids": [good, missing], "action": "mark_ok"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == 1
        assert body["failed"] == 1
        by_id = {r["row_id"]: r for r in body["results"]}
        assert by_id[good]["ok"] is True
        assert by_id[missing]["ok"] is False
    finally:
        _delete_company(co)
