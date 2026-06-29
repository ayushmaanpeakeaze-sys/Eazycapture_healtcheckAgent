"""Tenant-scoping guard for every route that touches multi-tenant data.

This module is THE one point at which a request's ``company_id`` is
validated AND access-checked against the caller:

* The company must exist and be active (else 404).
* Firm isolation: the company must belong to the caller's firm — a company
  in another firm reads as 404 (never confirmed to exist).
* Admins may access any company in their firm.
* Team members may access ONLY the companies assigned to them in
  ``user_company_access`` (else 403), within their firm.

The firm-less super-admin (created via ``scripts/create_admin``) has no firm
and sees every company — it is the platform operator, not a tenant.

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
    * 404 if the company belongs to another firm (cross-firm = invisible).
    * Admin → any active company in their firm is allowed.
    * Team member → must be assigned to the company, else 403.
    """
    record = (
        await db.execute(
            select(Company.id, Company.is_active, Company.firm_id).where(
                Company.id == company_id
            )
        )
    ).first()
    if record is None or not record.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown company.",
        )

    # Demo / auth-disabled mode has no real user → no firm context, allow.
    if user.user_id is None:
        return record.id

    db_user = (
        await db.execute(
            select(User.firm_id, User.company_access_mode).where(User.id == user.user_id)
        )
    ).first()
    user_firm_id = db_user.firm_id if db_user else None
    mode = db_user.company_access_mode if db_user else None

    # Firm isolation: a company outside the caller's firm reads as 404, so a
    # cross-firm id is indistinguishable from a non-existent one. (A firm-less
    # super-admin skips this and can reach any company.)
    if user_firm_id is not None and record.firm_id != user_firm_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown company.",
        )

    # Admins, and team members in "all" mode, skip the per-company assignment.
    if user.is_admin or mode == "all":
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

    * ``None``  → no restriction: demo mode or the firm-less super-admin.
    * ``list``  → the companies the caller may see (admin / "all" mode →
                  every company in their firm; "selected" → assigned ∩ firm).
                  May be empty → they see nothing.
    """
    # Demo / auth-disabled → unrestricted.
    if user.user_id is None:
        return None

    db_user = (
        await db.execute(
            select(User.firm_id, User.company_access_mode).where(User.id == user.user_id)
        )
    ).first()
    user_firm_id = db_user.firm_id if db_user else None
    mode = db_user.company_access_mode if db_user else None

    # Firm-less super-admin (created via script) → unrestricted.
    if user_firm_id is None:
        if user.is_admin or mode == "all":
            return None
        rows = await db.execute(
            select(UserCompanyAccess.company_id).where(
                UserCompanyAccess.user_id == user.user_id,
            )
        )
        return [r[0] for r in rows.all()]

    # Firm-scoped: every company in the caller's firm (the active/inactive
    # split is the caller's concern — e.g. the disconnected-orgs view wants
    # inactive ones).
    firm_company_ids = [
        r[0]
        for r in (
            await db.execute(
                select(Company.id).where(Company.firm_id == user_firm_id)
            )
        ).all()
    ]
    if user.is_admin or mode == "all":
        return firm_company_ids

    # "selected" mode → assigned companies intersected with the firm.
    assigned = {
        r[0]
        for r in (
            await db.execute(
                select(UserCompanyAccess.company_id).where(
                    UserCompanyAccess.user_id == user.user_id,
                )
            )
        ).all()
    }
    return [cid for cid in firm_company_ids if cid in assigned]
