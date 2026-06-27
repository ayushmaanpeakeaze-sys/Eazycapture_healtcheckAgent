"""Day 6 — Nango integration tests.

These never hit the real ``api.nango.dev``: we either drive
:class:`NangoClient` with an empty secret (which short-circuits to
``None``) or monkey-patch :class:`NangoService` methods.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import Company, HealthCheckResult
from app.modules.integrations.nango.client import NangoClient
from app.modules.integrations.nango.service import NangoService


# ---------- fixtures ------------------------------------------------

@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


def _insert_company(
    name: str,
    *,
    nango_connection_id: str | None = None,
    xero_tenant_id: str | None = None,
) -> uuid.UUID:
    cid = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(Company(
            id=cid, name=name, is_active=True,
            nango_connection_id=nango_connection_id,
            xero_tenant_id=xero_tenant_id,
        ))
        db.commit()
    return cid


def _delete_company(cid: uuid.UUID) -> None:
    with SyncSessionLocal() as db:
        c = db.get(Company, cid)
        if c is not None:
            db.delete(c)
            db.commit()


def _insert_trapped(
    company_id: uuid.UUID,
    *,
    document_id: uuid.UUID | None = None,
    document_type: str = "ACCPAY",
) -> tuple[uuid.UUID, uuid.UUID]:
    row_id = uuid.uuid4()
    doc_id = document_id or uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(HealthCheckResult(
            id=row_id, company_id=company_id, document_id=doc_id,
            document_type=document_type, kind="post_ledger",
            status="blocked", error_msgs="fixture",
            result={"flagged": [{"rule_id": "fixture"}],
                    "messages": "fixture"},
        ))
        db.commit()
    return row_id, doc_id


# =====================================================================
# 1. NangoClient short-circuits when no secret key is set.
# =====================================================================

async def test_nango_client_disabled_returns_none():
    """With an empty secret, every Nango call returns None without
    touching the wire."""
    client = NangoClient(secret_key="")
    assert client._is_enabled() is False

    assert await client.proxy_get(
        "conn", "xero", "api.xro/2.0/Invoices", tenant_id="t",
    ) is None
    assert await client.proxy_post(
        "conn", "xero", "api.xro/2.0/Invoices/x",
        tenant_id="t", json_body={"x": 1},
    ) is None
    assert await client.get_connection("conn", "xero") is None
    assert await client.create_connect_session("user-1", ["xero"]) is None


async def test_nango_service_is_available_reflects_secret():
    """``is_available()`` is the branch flag every caller uses; make
    sure it tracks the underlying client's enabled state."""
    disabled = NangoService(client=NangoClient(secret_key=""))
    enabled = NangoService(client=NangoClient(secret_key="secret_xxx"))
    assert disabled.is_available() is False
    assert enabled.is_available() is True


# =====================================================================
# 2. resolve falls back to stub when Nango is unavailable.
# =====================================================================

async def test_resolve_falls_back_to_stub_when_nango_unavailable(
    async_client: httpx.AsyncClient,
):
    co = _insert_company(
        "Nango stub-fallback test",
        nango_connection_id=None,   # ← no connection on the company
        xero_tenant_id=None,
    )
    try:
        row_id, _ = _insert_trapped(co)

        resp = await async_client.post(
            f"/api/v1/health/trapped/{row_id}/resolve/?company_id={co}",
            json={"field_updates": {"Status": "VOIDED"},
                  "resolution_notes": "demo"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        # The stub fallback must be the path taken — even if the
        # global Nango secret were set, the per-company connection
        # is missing, so the resolve service short-circuits.
        assert body["xero_response"]["stub"] is True
        assert "Nango disabled or company missing connection" in (
            body["xero_response"].get("reason") or ""
        )
    finally:
        _delete_company(co)


# =====================================================================
# 3. audit fetch chooses the seed branch when no connection exists.
# =====================================================================

async def test_audit_uses_seed_when_no_connection(monkeypatch):
    """``_fetch_audit_transactions`` must pick the seeded local path
    when the company has no ``nango_connection_id`` — even if the
    global Nango client *were* enabled. We patch ``is_available`` so
    the negative branch is the company-level check, not the secret.

    We deliberately scrub Demo Co's connection fields at test start
    rather than assert they're pristine — earlier live demo runs may
    have left a connection id on the row, and the test's purpose is
    the routing logic, not the demo state.
    """
    from app.modules.healthcheck import tasks
    from app.modules.healthcheck.seed_data import DEMO_CO_ID

    monkeypatch.setattr(
        NangoService, "is_available", lambda self: True,
    )
    nango_invoice_calls: list[tuple] = []
    async def _fake_invoices(*args, **kwargs):
        nango_invoice_calls.append((args, kwargs))
        return []
    monkeypatch.setattr(
        NangoService, "fetch_xero_invoices_page", _fake_invoices,
    )

    # Snapshot + clear the Demo Co connection fields for the duration of
    # the test, then restore them at teardown so other tests / demos
    # aren't affected.
    with SyncSessionLocal() as db:
        company = db.get(Company, DEMO_CO_ID)
        assert company is not None, "seed should have run by now"
        original_conn = company.nango_connection_id
        original_tenant = company.xero_tenant_id
        company.nango_connection_id = None
        company.xero_tenant_id = None
        db.commit()
    try:
        with SyncSessionLocal() as db:
            company = db.get(Company, DEMO_CO_ID)
            transactions, source = tasks._fetch_audit_transactions(db, company)

        assert source == "seed"
        assert len(transactions) > 0  # Demo Co has 22 seeded invoices
        # Critical: the company guard must short-circuit BEFORE we hit
        # the Nango wire, so no Nango calls occurred.
        assert nango_invoice_calls == []
    finally:
        with SyncSessionLocal() as db:
            company = db.get(Company, DEMO_CO_ID)
            company.nango_connection_id = original_conn
            company.xero_tenant_id = original_tenant
            db.commit()


def test_audit_fails_visibly_on_xero_auth_error(monkeypatch):
    """A CONNECTED company whose Xero pull fails (expired/revoked token → 401/403)
    must NOT silently fall back to stale seed data. ``_fetch_audit_transactions``
    raises a clear 'reconnect Xero' error so the run surfaces it to the user.

    Sync test on purpose: ``_fetch_audit_transactions`` uses ``asyncio.run`` the
    way the worker does, which needs no already-running event loop.
    """
    from app.modules.healthcheck import tasks
    from app.modules.healthcheck.seed_data import DEMO_CO_ID
    from app.modules.integrations.nango.client import NangoAuthError

    monkeypatch.setattr(NangoService, "is_available", lambda self: True)

    async def _raise_auth(*args, **kwargs):
        # Mirrors NangoClient._send raising on a 401/403 GET.
        raise NangoAuthError("Xero rejected the request (HTTP 403).")
    monkeypatch.setattr(NangoService, "fetch_xero_invoices_page", _raise_auth)

    with SyncSessionLocal() as db:
        company = db.get(Company, DEMO_CO_ID)
        original_conn = company.nango_connection_id
        original_tenant = company.xero_tenant_id
        company.nango_connection_id = "conn-broken"
        company.xero_tenant_id = "tenant-broken"
        db.commit()
    try:
        with SyncSessionLocal() as db:
            company = db.get(Company, DEMO_CO_ID)
            with pytest.raises(RuntimeError, match="(?i)reconnect xero"):
                tasks._fetch_audit_transactions(db, company)
    finally:
        with SyncSessionLocal() as db:
            company = db.get(Company, DEMO_CO_ID)
            company.nango_connection_id = original_conn
            company.xero_tenant_id = original_tenant
            db.commit()


async def test_send_raises_on_auth_failure_for_reads_only(monkeypatch):
    """``NangoClient._send`` surfaces a 401/403 on a GET (so the audit can't
    mistake it for 'no data'), but keeps returning None on a POST (write actions
    already handle a failed call)."""
    import app.modules.integrations.nango.client as client_mod
    from app.modules.integrations.nango.client import NangoAuthError

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **k):
            return httpx.Response(403, text="Forbidden",
                                  request=httpx.Request(method, url))

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeAsyncClient)
    client = NangoClient(secret_key="secret_xxx")

    # GET 403 → raises (connection broken, must surface)
    with pytest.raises(NangoAuthError):
        await client._send("GET", "http://x/proxy/api.xro/2.0/Invoices", headers={})
    # POST 403 → returns None (write callers already handle a failed action)
    assert await client._send(
        "POST", "http://x/proxy/api.xro/2.0/Invoices", headers={},
    ) is None
