"""Firm-level (workspace) isolation.

Each signup is a separate firm; a firm must never see another firm's orgs.
These tests exercise the two choke points that enforce it —
``get_current_company_id`` (single-company access) and
``allowed_company_ids_for`` (cross-company views) — against a real Postgres,
plus the self-service ``/auth/register`` flow.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import httpx
import pytest
from fastapi import HTTPException

from app.core.auth import CurrentUser
from app.core.db import AsyncSessionLocal
from app.core.multi_tenant import allowed_company_ids_for, get_current_company_id
from app.main import app
from app.modules.auth.models import Firm, User
from app.modules.healthcheck.models import Company


@pytest.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def two_firms() -> AsyncIterator[dict[str, uuid.UUID]]:
    """Firm A and Firm B, each with one admin + one company, plus a firm-less
    super-admin. Tearing down the firms cascades their users + companies."""
    ids = {
        "firm_a": uuid.uuid4(), "firm_b": uuid.uuid4(),
        "admin_a": uuid.uuid4(), "admin_b": uuid.uuid4(),
        "company_a": uuid.uuid4(), "company_b": uuid.uuid4(),
        "super_admin": uuid.uuid4(),
    }
    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(Firm(id=ids["firm_a"], name="Firm A (test)"))
            db.add(Firm(id=ids["firm_b"], name="Firm B (test)"))
            db.add(User(
                id=ids["admin_a"], firm_id=ids["firm_a"],
                email=f"a-{ids['admin_a']}@test.local", role="admin",
                status="active", company_access_mode="all",
            ))
            db.add(User(
                id=ids["admin_b"], firm_id=ids["firm_b"],
                email=f"b-{ids['admin_b']}@test.local", role="admin",
                status="active", company_access_mode="all",
            ))
            db.add(User(
                id=ids["super_admin"], firm_id=None,
                email=f"root-{ids['super_admin']}@test.local", role="admin",
                status="active", company_access_mode="all",
            ))
            db.add(Company(id=ids["company_a"], firm_id=ids["firm_a"], name="Co A (firm test)", is_active=True))
            db.add(Company(id=ids["company_b"], firm_id=ids["firm_b"], name="Co B (firm test)", is_active=True))

    yield ids

    async with AsyncSessionLocal() as db:
        async with db.begin():
            sa = await db.get(User, ids["super_admin"])
            if sa is not None:
                await db.delete(sa)
            for fid in (ids["firm_a"], ids["firm_b"]):
                firm = await db.get(Firm, fid)
                if firm is not None:
                    await db.delete(firm)  # cascades its users + companies


def _admin(user_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(user_id=user_id, email="x@test.local", role="admin")


async def test_allowed_company_ids_is_firm_scoped(two_firms):
    async with AsyncSessionLocal() as db:
        allowed = await allowed_company_ids_for(db, _admin(two_firms["admin_a"]))
    assert allowed is not None
    assert two_firms["company_a"] in allowed
    assert two_firms["company_b"] not in allowed


async def test_get_current_company_id_allows_own_firm(two_firms):
    async with AsyncSessionLocal() as db:
        cid = await get_current_company_id(
            two_firms["company_a"], db, _admin(two_firms["admin_a"]),
        )
    assert cid == two_firms["company_a"]


async def test_get_current_company_id_blocks_cross_firm(two_firms):
    """Admin A asking for Firm B's company → 404 (never confirmed to exist)."""
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await get_current_company_id(
                two_firms["company_b"], db, _admin(two_firms["admin_a"]),
            )
    assert exc.value.status_code == 404


async def test_firmless_superadmin_is_unrestricted(two_firms):
    """The platform super-admin (no firm) sees everything → None."""
    async with AsyncSessionLocal() as db:
        allowed = await allowed_company_ids_for(db, _admin(two_firms["super_admin"]))
        cid = await get_current_company_id(
            two_firms["company_b"], db, _admin(two_firms["super_admin"]),
        )
    assert allowed is None
    assert cid == two_firms["company_b"]


async def test_register_creates_isolated_firm(async_client: httpx.AsyncClient):
    email = f"signup-{uuid.uuid4()}@test.local"
    resp = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret123", "firm_name": "My Firm"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "admin"
    assert body["access_token"]
    user_id = uuid.UUID(body["user_id"])

    try:
        # A brand-new firm owns no companies yet.
        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
            assert user is not None and user.firm_id is not None
            allowed = await allowed_company_ids_for(db, _admin(user_id))
        assert allowed == []

        # Same email again → 409.
        dup = await async_client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret123"},
        )
        assert dup.status_code == 409
    finally:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                user = await db.get(User, user_id)
                if user is not None:
                    firm = await db.get(Firm, user.firm_id)
                    if firm is not None:
                        await db.delete(firm)  # cascades the user
