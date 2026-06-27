"""Outbound notifications.

A small, extensible layer so the app can reach people across platforms.
Today: email. Tomorrow: WhatsApp / SMS / Slack — each a new module under
``channels/`` registered in :class:`~app.modules.notifications.service.NotificationService`.

Usage::

    from app.modules.notifications import notification_service, Recipient
    from app.modules.notifications.templates import invite_email

    result = await notification_service.send(
        recipient=Recipient(email=user.email, name=user.full_name),
        message=invite_email(accept_url=url, expires_days=7),
    )
    if not result.ok:
        ...  # result.detail explains why; the action itself still succeeded
"""
from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)
from app.modules.notifications.service import NotificationService, notification_service

__all__ = [
    "DeliveryResult",
    "Message",
    "NotificationChannel",
    "Recipient",
    "NotificationService",
    "notification_service",
]
