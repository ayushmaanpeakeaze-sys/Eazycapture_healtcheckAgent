"""Delivery tracking — persists notification sends and applies provider
delivery events (delivered/bounced/complained) back onto the log + user.

Kept separate from the channels (pure delivery) and the service (routing)
so those stay DB-agnostic. This is the only notifications module that
touches the database.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.notifications.channels.base import DeliveryResult
from app.modules.notifications.models import NotificationLog

logger = logging.getLogger("eazycapture.notifications.persistence")

# Map raw provider event names to normalized status. ``None`` = ignore
# (transient events like 'deferred'/'processed' must not overwrite status).
_EVENT_MAP: dict[str, Optional[str]] = {
    "delivered": "delivered",
    "delivery": "delivered",
    "open": "delivered",          # an open implies delivery
    "click": "delivered",
    "bounce": "bounced",
    "bounced": "bounced",
    "hard_bounce": "bounced",
    "hardbounce": "bounced",
    "dropped": "bounced",
    "complained": "complained",
    "complaint": "complained",
    "spamreport": "complained",
    "spam": "complained",
    "failed": "failed",
    "deferred": None,
    "processed": None,
    "queued": None,
    "sent": None,
}

# Statuses that flag a problem the admin should see.
BAD_STATUSES = {"bounced", "complained", "failed"}


def normalize_event(event: Optional[str]) -> Optional[str]:
    return _EVENT_MAP.get((event or "").strip().lower(), None)


async def record_send(
    db: AsyncSession,
    *,
    recipient_email: str,
    kind: str,
    delivery: DeliveryResult,
    user_id: Optional[UUID] = None,
    provider_message_id: Optional[str] = None,
) -> NotificationLog:
    """Write one notification_log row for a send attempt and mirror the
    status onto ``User.email_status``. Commits."""
    send_status = "sent" if delivery.ok else "failed"
    row = NotificationLog(
        user_id=user_id,
        recipient_email=(recipient_email or "").strip().lower(),
        channel=delivery.channel,           # actual transport used (email/console)
        kind=kind,
        status=send_status,
        provider=delivery.channel,
        provider_message_id=provider_message_id,
        error=None if delivery.ok else delivery.detail,
    )
    db.add(row)
    if user_id is not None:
        user = await db.get(User, user_id)
        if user is not None:
            user.email_status = send_status
    await db.commit()
    return row


async def apply_delivery_event(
    db: AsyncSession,
    *,
    email: Optional[str],
    event: str,
    provider: Optional[str] = None,
    message_id: Optional[str] = None,
) -> bool:
    """Apply one provider event. Updates the matching log row (by message id
    if known, else the latest row for that recipient) and the linked user's
    ``email_status``. Returns True if anything was updated. Commits."""
    status = normalize_event(event)
    if status is None:
        return False
    email_norm = (email or "").strip().lower()

    if not message_id and not email_norm:
        return False

    # Match by provider message id first; fall back to the latest log for this
    # recipient, since SMTP sends have no message id at log time but the webhook does.
    row: Optional[NotificationLog] = None
    if message_id:
        row = (
            await db.execute(
                select(NotificationLog)
                .where(NotificationLog.provider_message_id == message_id)
                .order_by(NotificationLog.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
    if row is None and email_norm:
        row = (
            await db.execute(
                select(NotificationLog)
                .where(NotificationLog.recipient_email == email_norm)
                .order_by(NotificationLog.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

    updated = False
    target_user_id: Optional[UUID] = None
    if row is not None:
        row.status = status
        if provider:
            row.provider = provider
        # Backfill the id so later events for the same message match directly.
        if message_id and not row.provider_message_id:
            row.provider_message_id = message_id
        target_user_id = row.user_id
        updated = True

    # Flag the user even when no log row exists, matching by email address.
    user = None
    if target_user_id is not None:
        user = await db.get(User, target_user_id)
    if user is None and email_norm:
        user = (
            await db.execute(select(User).where(User.email == email_norm))
        ).scalar_one_or_none()
    if user is not None:
        user.email_status = status
        updated = True

    if updated:
        await db.commit()
        logger.info("delivery event '%s' → status '%s' for %s",
                    event, status, email_norm or message_id)
    return updated
