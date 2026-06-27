"""Nango webhook handler test — multi-org onboarding.

The webhook keys off the connecting ACCOUNTANT (a User), enumerates every
Xero org the grant covers, and creates one Company per org linked to that
user. We mock tenant enumeration (no live Nango) and audit dispatch (no
Celery/Redis) to isolate the webhook's create-companies-per-org logic.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.auth.models import User, UserCompanyAccess
from app.modules.healthcheck.models import Company
from app.modules.healthcheck.services.audit_service import AuditService
from app.modules.integrations.service import IntegrationService


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


def _insert_user(email: str) -> uuid.UUID:
    uid = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(User(id=uid, email=email, role="admin", status="active"))
        db.commit()
    return uid


def _cleanup(user_id: uuid.UUID, connection_id: str) -> None:
    with SyncSessionLocal() as db:
        companies = db.execute(
            select(Company).where(Company.nango_connection_id == connection_id)
        ).scalars().all()
        for c in companies:
            db.delete(c)  # cascades to UserCompanyAccess
        u = db.get(User, user_id)
        if u is not None:
            db.delete(u)
        db.commit()


async def test_webhook_creates_one_company_per_org(
    async_client: httpx.AsyncClient,
    monkeypatch,
):
    """One connection covering 2 Xero orgs → 2 Company rows, both linked to
    the connecting accountant, both sharing the connection_id."""
    user_id = _insert_user(f"accountant-{uuid.uuid4()}@firm.com")
    connection_id = "test-conn-multiorg-001"
    tenant_a = "tenant-aaa-111"
    tenant_b = "tenant-bbb-222"

    try:
        # Mock: this grant covers TWO organisations.
        async def _fake_list_tenants(self, conn_id: str) -> list[dict[str, Any]]:
            assert conn_id == connection_id
            return [
                {"tenant_id": tenant_a, "tenant_name": "Acme Ltd", "tenant_type": "ORGANISATION"},
                {"tenant_id": tenant_b, "tenant_name": "Beta Co", "tenant_type": "ORGANISATION"},
            ]
        monkeypatch.setattr(IntegrationService, "list_tenants", _fake_list_tenants)

        # Mock: audit dispatch is a no-op (no Celery/Redis in tests).
        dispatched: list[str] = []
        async def _fake_dispatch(self, company_id):
            dispatched.append(str(company_id))
        monkeypatch.setattr(AuditService, "dispatch_audit", _fake_dispatch)

        payload = {
            "type": "auth",
            "operation": "creation",
            "providerConfigKey": "xero",
            "connectionId": connection_id,
            "endUser": {"endUserId": str(user_id)},
        }
        resp = await async_client.post("/api/v1/webhooks/nango", json=payload)
        assert resp.status_code == 202, resp.text
        assert resp.json() == {"accepted": True}

        # Two Company rows, one per org, both on this connection.
        with SyncSessionLocal() as db:
            companies = db.execute(
                select(Company).where(Company.nango_connection_id == connection_id)
            ).scalars().all()
            by_tenant = {c.xero_tenant_id: c for c in companies}
            assert set(by_tenant) == {tenant_a, tenant_b}
            assert by_tenant[tenant_a].name == "Acme Ltd"
            assert by_tenant[tenant_b].name == "Beta Co"

            # Both linked to the connecting accountant.
            for c in companies:
                link = db.execute(
                    select(UserCompanyAccess.id).where(
                        UserCompanyAccess.user_id == user_id,
                        UserCompanyAccess.company_id == c.id,
                    )
                ).scalar_one_or_none()
                assert link is not None, f"no access link for {c.xero_tenant_id}"

            # User's connection recorded for reconcile.
            u = db.get(User, user_id)
            assert u.nango_connection_id == connection_id

        # Both new orgs were queued for an audit.
        assert len(dispatched) == 2
    finally:
        _cleanup(user_id, connection_id)


async def test_webhook_is_idempotent_on_duplicate_delivery(
    async_client: httpx.AsyncClient,
    monkeypatch,
):
    """Nango may deliver auth.creation more than once — a second delivery
    must not create duplicate Company rows."""
    user_id = _insert_user(f"accountant-{uuid.uuid4()}@firm.com")
    connection_id = "test-conn-idem-002"
    tenant = "tenant-idem-999"

    try:
        async def _fake_list_tenants(self, conn_id: str) -> list[dict[str, Any]]:
            return [{"tenant_id": tenant, "tenant_name": "Idem Co", "tenant_type": "ORGANISATION"}]
        monkeypatch.setattr(IntegrationService, "list_tenants", _fake_list_tenants)

        async def _fake_dispatch(self, company_id):
            pass
        monkeypatch.setattr(AuditService, "dispatch_audit", _fake_dispatch)

        payload = {
            "type": "auth", "operation": "creation", "providerConfigKey": "xero",
            "connectionId": connection_id, "endUser": {"endUserId": str(user_id)},
        }
        await async_client.post("/api/v1/webhooks/nango", json=payload)
        await async_client.post("/api/v1/webhooks/nango", json=payload)  # duplicate

        with SyncSessionLocal() as db:
            companies = db.execute(
                select(Company).where(Company.nango_connection_id == connection_id)
            ).scalars().all()
            assert len(companies) == 1, "duplicate delivery created duplicate rows"
            links = db.execute(
                select(UserCompanyAccess).where(
                    UserCompanyAccess.company_id == companies[0].id,
                )
            ).scalars().all()
            assert len(links) == 1, "duplicate access link created"
    finally:
        _cleanup(user_id, connection_id)
