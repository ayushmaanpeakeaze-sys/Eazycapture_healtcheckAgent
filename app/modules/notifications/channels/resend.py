"""Resend HTTP email channel.

Sends via the Resend API over HTTPS, so it works where outbound SMTP is
blocked (e.g. Railway blocks port 587). Enabled when ``RESEND_API_KEY`` is set;
otherwise it reports itself unconfigured and the service falls back to SMTP or
the console channel.
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

logger = logging.getLogger("eazycapture.notifications.resend")

_API_URL = "https://api.resend.com/emails"
_TIMEOUT_S = 15.0


class ResendEmailChannel(NotificationChannel):
    name = "resend"

    def __init__(self) -> None:
        self._api_key = (settings.RESEND_API_KEY or "").strip()
        self._from = (settings.RESEND_FROM or "onboarding@resend.dev").strip()

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def can_handle(self, recipient: Recipient) -> bool:
        return self.is_available and bool(recipient.email)

    async def send(self, recipient: Recipient, message: Message) -> DeliveryResult:
        if not self.is_available:
            return DeliveryResult(self.name, ok=False, detail="RESEND_API_KEY not set")
        if not recipient.email:
            return DeliveryResult(self.name, ok=False, detail="recipient has no email")

        payload: dict[str, object] = {
            "from": self._from,
            "to": [recipient.email],
            "subject": message.subject,
            "text": message.body_text,
        }
        if message.body_html:
            payload["html"] = message.body_html

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    _API_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except Exception as exc:  # noqa: BLE001 — never raise to the caller
            logger.warning("Resend send error to %s: %s", recipient.email, exc)
            return DeliveryResult(self.name, ok=False, detail=str(exc)[:200])

        if resp.status_code in (200, 201):
            message_id = ""
            try:
                message_id = str(resp.json().get("id", ""))
            except ValueError:
                pass
            return DeliveryResult(self.name, ok=True, detail=message_id)

        logger.warning(
            "Resend send to %s failed: %s %s",
            recipient.email, resp.status_code, resp.text[:200],
        )
        return DeliveryResult(
            self.name, ok=False, detail=f"{resp.status_code}: {resp.text[:200]}",
        )
