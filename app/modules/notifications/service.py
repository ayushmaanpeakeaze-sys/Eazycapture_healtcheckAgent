"""NotificationService — the one entry point the app uses to send anything.

It owns the channel registry and the routing policy. Callers say *what* to
send and *to whom*; the service decides *how* (which channel), or honours an
explicit ``channel=`` override.

Adding a platform later (WhatsApp, SMS, Slack):
    1. add ``channels/whatsapp.py`` implementing ``NotificationChannel``,
    2. register it in ``_build_channels`` below,
    3. (optionally) teach ``_auto_pick`` to prefer it for phone recipients.
No caller changes needed.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)
from app.modules.notifications.channels.console import ConsoleChannel
from app.modules.notifications.channels.email import SmtpEmailChannel
from app.modules.notifications.channels.mailgun import MailgunEmailChannel
from app.modules.notifications.channels.resend import ResendEmailChannel

logger = logging.getLogger("eazycapture.notifications")


class NotificationService:
    def __init__(self) -> None:
        self._mailgun = MailgunEmailChannel()
        self._resend = ResendEmailChannel()
        self._email = SmtpEmailChannel()
        self._console = ConsoleChannel()
        self._channels: dict[str, NotificationChannel] = self._build_channels()

    def _build_channels(self) -> dict[str, NotificationChannel]:
        # Register every available channel here. Order is irrelevant —
        # routing is by name / capability, not list position.
        channels: list[NotificationChannel] = [
            self._mailgun, self._resend, self._email, self._console,
        ]
        return {c.name: c for c in channels}

    def channel(self, name: str) -> Optional[NotificationChannel]:
        return self._channels.get(name)

    async def send(
        self,
        *,
        recipient: Recipient,
        message: Message,
        channel: Optional[str] = None,
    ) -> DeliveryResult:
        """Deliver ``message`` to ``recipient``.

        ``channel`` forces a specific transport by name; omit it to let the
        service auto-pick (real channel if configured, else console). Always
        returns a :class:`DeliveryResult` — never raises — so a failed send
        never breaks the caller's flow.
        """
        if channel is not None:
            ch = self._channels.get(channel)
            if ch is None:
                return DeliveryResult(channel, ok=False, detail="unknown channel")
            if not ch.can_handle(recipient):
                return DeliveryResult(
                    channel, ok=False, detail="channel cannot reach this recipient",
                )
            return await ch.send(recipient, message)
        return await self._auto_pick(recipient, message)

    async def _auto_pick(
        self, recipient: Recipient, message: Message,
    ) -> DeliveryResult:
        """Pick the best configured channel for the recipient. Prefer Mailgun
        (the firm's primary provider), then Resend, then SMTP, else the console.
        Mailgun/Resend send over HTTPS so they work where outbound SMTP is
        blocked (e.g. Railway). Extend here when phone channels (WhatsApp/SMS) land."""
        if self._mailgun.can_handle(recipient):
            return await self._mailgun.send(recipient, message)
        if self._resend.can_handle(recipient):
            return await self._resend.send(recipient, message)
        if self._email.can_handle(recipient):
            return await self._email.send(recipient, message)
        return await self._console.send(recipient, message)


# Module-level singleton — channels read settings at construction time.
notification_service = NotificationService()
