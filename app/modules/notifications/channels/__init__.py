"""Notification channels — one module per delivery transport."""
from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)
from app.modules.notifications.channels.console import ConsoleChannel
from app.modules.notifications.channels.email import SmtpEmailChannel

__all__ = [
    "DeliveryResult",
    "Message",
    "NotificationChannel",
    "Recipient",
    "ConsoleChannel",
    "SmtpEmailChannel",
]
