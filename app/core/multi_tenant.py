"""Tenant-scoping guard for every route that touches multi-tenant data.

This module is THE one point at which a request's ``company_id`` is
validated AND access-checked against the caller's role:

* The company must exist and be active (else 404).
* Admins may access any company.
* Team members may access ONLY the companies assigned to them in
  ``user_company_access`` (else 403).

Repository-layer enforcement is the second line of defence: every
repository method *also* takes ``company_id`` as a required argument
and includes it in every WHERE clause. The two layers together make a
cross-tenant leak require two simultaneous bugs.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.db import get_db
from app.modules.auth.models import User, UserCompanyAccess
from app.modules.healthcheck.models import Company


async def get_current_company_id(
    company_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> UUID:
    """Validate + access-check ``company_id`` for the current user.

    * 404 if the company doesn't exist or is deactivated (never 403 on
      existence, so a stranger can't probe valid company ids).
    * Admin → any active company is allowed.
    * Team member → must be assigned to the company, else 403.
    """
    row = await db.execute(
        select(Company.id, Company.is_active).where(Company.id == company_id)
    )
    record = row.first()
    if record is None or not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown company.",
        )

    # Admins (and POC mode, which presents as admin) skip the assignment check.
    if user.is_admin:
        return record.id

    # Team member in "all" mode → access to every active company,
    # including future ones. No per-company assignment needed.
    db_user = await db.execute(
        select(User.company_access_mode).where(User.id == user.user_id)
    )
    mode = db_user.scalar_one_or_none()
    if mode == "all":
        return record.id

    # "selected" mode → must be explicitly assigned to this company.
    assigned = await db.execute(
        select(UserCompanyAccess.id).where(
            UserCompanyAccess.user_id == user.user_id,
            UserCompanyAccess.company_id == company_id,
        ).limit(1)
    )
    if assigned.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this company.",
        )
    return record.id


async def allowed_company_ids_for(
    db: AsyncSession,
    user: CurrentUser,
) -> list[UUID] | None:
    """Return the company-id whitelist for cross-company views (panorama).

    * ``None``  → no restriction (admin, or team member in "all" mode):
                  show every company.
    * ``list``  → team member in "selected" mode: only these companies
                  (may be empty → they see nothing).
    """
    if user.is_admin or user.user_id is None:
        return None

    mode = (
        await db.execute(
            select(User.company_access_mode).where(User.id == user.user_id)
        )
    ).scalar_one_or_none()
    if mode == "all":
        return None

    rows = await db.execute(
        select(UserCompanyAccess.company_id).where(
            UserCompanyAccess.user_id == user.user_id,
        )
    )
    return [r[0] for r in rows.all()]
