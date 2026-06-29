"""Auth dependencies for the RBAC layer.

This service issues and validates its OWN JWTs (it is independent of the
main EazyCapture app). Tokens are minted by ``app.core.security`` on
login / invite-accept.

Dependencies:
* ``get_current_user``      — decode the bearer token → ``CurrentUser``.
                              In demo mode (no ``JWT_SECRET`` or
                              ``AUTH_DISABLED=true``) returns a synthetic
                              admin so existing demos keep working.
* ``require_admin``         — 403 unless the caller is an admin.
* ``get_current_company_id``— (in multi_tenant.py) scopes the request to
                              one company AND, for team members, verifies
                              they're assigned to it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import decode_access_token

logger = logging.getLogger("eazycapture.auth")

ROLE_ADMIN = "admin"
ROLE_TEAM_MEMBER = "team_member"


@dataclass(frozen=True)
class CurrentUser:
    user_id: Optional[UUID]
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


# Synthetic user used in demo mode (auth disabled) so the UI stays open.
_DEMO_USER = CurrentUser(user_id=None, email="demo@local", role=ROLE_ADMIN)


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Decode the bearer token into a :class:`CurrentUser`.

    * ``JWT_SECRET`` unset or ``AUTH_DISABLED`` set → demo mode → returns
      a synthetic admin.
    * Missing/invalid token → 401.
    * Valid token → re-checked against the DB so a disabled or deleted
      account is rejected immediately (revocation), not honoured until the
      token expires. Identity (role/email) is taken from the DB row, so an
      admin's role change also takes effect on the user's next request.
    """
    if settings.AUTH_DISABLED or not (settings.JWT_SECRET or "").strip():
        return _DEMO_USER

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    raw_sub = claims.get("sub")
    try:
        user_id = UUID(str(raw_sub)) if raw_sub else None
    except (TypeError, ValueError):
        user_id = None

    # Revocation: a valid signature isn't enough — the account must still
    # exist and be active. Lazy import keeps this core module free of a
    # hard dependency on the auth feature module at import time.
    if user_id is not None:
        from app.modules.auth.models import User

        db_user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if db_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account no longer exists.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if db_user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account has been disabled.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return CurrentUser(
            user_id=db_user.id, email=db_user.email, role=db_user.role,
        )

    return CurrentUser(
        user_id=user_id,
        email=str(claims.get("email") or ""),
        role=str(claims.get("role") or ROLE_TEAM_MEMBER),
    )


async def require_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """403 unless the caller is an admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user
