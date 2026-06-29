"""Mailgun HTTP email channel.

The firm's primary email provider for invites + signup OTPs. Sends via the
Mailgun Messages API over HTTPS, so it works where outbound SMTP is blocked
(e.g. Railway blocks port 587), and is auto-picked ahead of every other channel
whenever it's configured.

Auth is HTTP Basic with username ``api`` and the (sending) API key as the
password. Region is whatever ``APP_MAILGUN_API_BASE_URL`` points at — EU
(``https://api.eu.mailgun.net``) or US (``https://api.mailgun.net``). When the
sending domain or key is missing the channel reports itself unconfigured and the
service falls back to Resend, SMTP, or the console.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)

logger = logging.getLogger("eazycapture.notifications.mailgun")

_TIMEOUT_S = 15.0


class MailgunEmailChannel(NotificationChannel):
    name = "mailgun"

    def __init__(self) -> None:
        # The sending key is purpose-built for the Messages endpoint; fall back
        # to the account API key so either env alone is enough to send.
        self._api_key = (
            settings.MAILGUN_SENDING_API_KEY or settings.MAILGUN_API_KEY or ""
        ).strip()
        self._domain = (settings.MAILGUN_SENDING_DOMAIN or "").strip()
        base = (settings.MAILGUN_API_BASE_URL or "https://api.mailgun.net").strip()
        self._base_url = base.rstrip("/")
        # Mailgun rejects a sender that isn't on the sending domain, so default
        # to a no-reply address on that domain unless APP_MAILGUN_FROM overrides.
        configured_from = (settings.MAILGUN_FROM or "").strip()
        if configured_from:
            self._from = configured_from
        elif self._domain:
            self._from = f"{settings.APP_NAME} <noreply@{self._domain}>"
        else:
            self._from = ""

    @property
    def is_available(self) -> bool:
        return bool(self._api_key and self._domain)

    def can_handle(self, recipient: Recipient) -> bool:
        return self.is_available and bool(recipient.email)

    @property
    def _messages_url(self) -> str:
        return f"{self._base_url}/v3/{self._domain}/messages"

    async def send(self, recipient: Recipient, message: Message) -> DeliveryResult:
        if not self.is_available:
            return DeliveryResult(
                self.name, ok=False,
                detail="Mailgun not configured (APP_MAILGUN_SENDING_DOMAIN / API key)",
            )
        if not recipient.email:
            return DeliveryResult(self.name, ok=False, detail="recipient has no email")

        to = (
            f"{recipient.name} <{recipient.email}>"
            if recipient.name
            else recipient.email
        )
        # Mailgun's Messages API takes form-encoded fields, not JSON.
        data: dict[str, str] = {
            "from": self._from,
            "to": to,
            "subject": message.subject,
            "text": message.body_text,
        }
        if message.body_html:
            data["html"] = message.body_html

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    self._messages_url,
                    auth=("api", self._api_key),
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001 — never raise to the caller
            logger.warning("Mailgun send error to %s: %s", recipient.email, exc)
            return DeliveryResult(self.name, ok=False, detail=str(exc)[:200])

        if resp.status_code in (200, 201):
            message_id = ""
            try:
                message_id = str(resp.json().get("id", ""))
            except ValueError:
                pass
            return DeliveryResult(self.name, ok=True, detail=message_id)

        logger.warning(
            "Mailgun send to %s failed: %s %s",
            recipient.email, resp.status_code, resp.text[:200],
        )
        return DeliveryResult(
            self.name, ok=False, detail=f"{resp.status_code}: {resp.text[:200]}",
        )


async def send_probe() -> DeliveryResult:
    """Send a one-line test email to ``APP_MAILGUN_PROBE_TO`` to verify Mailgun
    sending end-to-end (domain, key, region). Used by ops to confirm the
    integration without triggering a real invite. Never raises."""
    to = (settings.MAILGUN_PROBE_TO or "").strip()
    if not to:
        return DeliveryResult("mailgun", ok=False, detail="APP_MAILGUN_PROBE_TO not set")
    return await MailgunEmailChannel().send(
        Recipient(email=to),
        Message(
            subject=f"{settings.APP_NAME} Mailgun probe",
            body_text=(
                "This is a Mailgun configuration probe from "
                f"{settings.APP_NAME}. Receiving it confirms sending works."
            ),
        ),
    )
