"""Console (log) channel — the dev fallback.

When no real channel is configured (e.g. SMTP creds not set on a laptop),
the service routes here so the action still succeeds and the message — most
importantly the invite link — is visible in the API log. Never used in
production once SMTP (or another channel) is configured.
"""
from __future__ import annotations

import logging

from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)

logger = logging.getLogger("hcpoc.notifications.console")


class ConsoleChannel(NotificationChannel):
    name = "console"

    def can_handle(self, recipient: Recipient) -> bool:
        return True

    async def send(self, recipient: Recipient, message: Message) -> DeliveryResult:
        target = recipient.email or recipient.phone or recipient.name or "unknown"
        logger.warning(
            "[notification:console] not delivered (no real channel configured)\n"
            "  to:      %s\n"
            "  subject: %s\n"
            "  body:\n%s",
            target, message.subject, message.body_text,
        )
        return DeliveryResult(
            self.name, ok=True, detail=f"logged to console for {target}",
        )
