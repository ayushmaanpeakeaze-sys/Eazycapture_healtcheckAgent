"""Record firm-scoped activity/notification feed events.

The event rides along with the caller's own commit, so it's persisted
atomically with the action that triggered it (invite, accept, access change,
org connect/remove). Health-score *alerts* are derived live in the
notifications endpoint, not stored here.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import Notification


async def record_event(
    db: AsyncSession,
    *,
    firm_id: Optional[UUID],
    type: str,
    title: str,
    severity: str = "info",
    detail: Optional[str] = None,
    actor_email: Optional[str] = None,
    company_id: Optional[UUID] = None,
) -> None:
    """Add a notification row to the session (caller commits)."""
    db.add(
        Notification(
            firm_id=firm_id,
            type=type,
            severity=severity,
            title=title,
            detail=detail,
            actor_email=actor_email,
            company_id=company_id,
        )
    )
