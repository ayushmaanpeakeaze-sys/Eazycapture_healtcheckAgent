"""Admin team-management endpoints: enable / disable / remove, firm-scoped.

Guards the regression where ``_load_managed_user`` recursed into itself (every
manage action 500'd) and proves a firm admin cannot touch another firm's users.
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
from fastapi import HTTPException

from app.core.auth import CurrentUser
from app.core.db import AsyncSessionLocal
from app.modules.auth.models import Firm, User
from app.modules.auth.routers import disable_user, enable_user, remove_user


@pytest.fixture
async def firms_with_members() -> AsyncIterator[dict[str, uuid.UUID]]:
    ids = {
        "firm_a": uuid.uuid4(), "firm_b": uuid.uuid4(),
        "admin_a": uuid.uuid4(),
        "member_a": uuid.uuid4(), "member_b": uuid.uuid4(),
    }
    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(Firm(id=ids["firm_a"], name="Firm A (team test)"))
            db.add(Firm(id=ids["firm_b"], name="Firm B (team test)"))
            db.add(User(
                id=ids["admin_a"], firm_id=ids["firm_a"],
                email=f"admin-{ids['admin_a']}@test.local", role="admin",
                status="active", company_access_mode="all",
            ))
            db.add(User(
                id=ids["member_a"], firm_id=ids["firm_a"],
                email=f"m-{ids['member_a']}@test.local", role="team_member",
                status="active", company_access_mode="all",
            ))
            db.add(User(
                id=ids["member_b"], firm_id=ids["firm_b"],
                email=f"m-{ids['member_b']}@test.local", role="team_member",
                status="active", company_access_mode="all",
            ))
    yield ids
    async with AsyncSessionLocal() as db:
        async with db.begin():
            for fid in (ids["firm_a"], ids["firm_b"]):
                firm = await db.get(Firm, fid)
                if firm is not None:
                    await db.delete(firm)  # cascades users


def _admin(user_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(user_id=user_id, email="admin@test.local", role="admin")


async def test_disable_then_enable_member(firms_with_members):
    admin = _admin(firms_with_members["admin_a"])
    async with AsyncSessionLocal() as db:
        disabled = await disable_user(firms_with_members["member_a"], admin=admin, db=db)
        assert disabled.status == "disabled"
    async with AsyncSessionLocal() as db:
        enabled = await enable_user(firms_with_members["member_a"], admin=admin, db=db)
        assert enabled.status == "active"


async def test_remove_member(firms_with_members):
    admin = _admin(firms_with_members["admin_a"])
    async with AsyncSessionLocal() as db:
        result = await remove_user(firms_with_members["member_a"], admin=admin, db=db)
        assert result.removed is True


async def test_cannot_manage_another_firms_member(firms_with_members):
    """Firm A's admin acting on Firm B's member → 404 (never revealed)."""
    admin = _admin(firms_with_members["admin_a"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await disable_user(firms_with_members["member_b"], admin=admin, db=db)
    assert exc.value.status_code == 404


async def test_cannot_remove_own_admin_account(firms_with_members):
    admin = _admin(firms_with_members["admin_a"])
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await remove_user(firms_with_members["admin_a"], admin=admin, db=db)
    assert exc.value.status_code == 400
