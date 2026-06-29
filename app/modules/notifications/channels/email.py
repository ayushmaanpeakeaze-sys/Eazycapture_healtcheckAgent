"""SMTP email channel.

Uses the stdlib ``smtplib`` (blocking) inside ``asyncio.to_thread`` so it
works in the async request path without pulling in an extra dependency.
Supports either implicit SSL (port 465) or STARTTLS (port 587).

Configuration comes from env via :data:`app.core.config.settings`:

* ``SMTP_HOST`` / ``SMTP_PORT``
* ``SMTP_USERNAME`` / ``SMTP_PASSWORD``   (omit for an open relay)
* ``SMTP_FROM``                            (defaults to the username)
* ``SMTP_SSL`` (465) or ``SMTP_STARTTLS`` (587, default)

When ``SMTP_HOST`` is empty the channel reports itself unconfigured and the
service falls back to the console channel — so local dev still works and
the invite link shows up in the API log.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings
from app.modules.notifications.channels.base import (
    DeliveryResult,
    Message,
    NotificationChannel,
    Recipient,
)

logger = logging.getLogger("eazycapture.notifications.email")

_TIMEOUT_S = 15.0


class SmtpEmailChannel(NotificationChannel):
    name = "email"

    def __init__(self) -> None:
        self._host = settings.SMTP_HOST.strip()
        self._port = settings.SMTP_PORT
        self._username = settings.SMTP_USERNAME.strip()
        # Gmail shows app passwords as "abcd efgh ijkl mnop" but rejects them
        # if the spaces are sent — strip all whitespace so a pasted-with-spaces
        # app password still authenticates.
        self._password = "".join((settings.SMTP_PASSWORD or "").split())
        self._from = (settings.SMTP_FROM or settings.SMTP_USERNAME).strip()
        self._use_ssl = settings.SMTP_SSL
        self._use_starttls = settings.SMTP_STARTTLS

    @property
    def configured(self) -> bool:
        """We can only send if we know a host and a From address."""
        return bool(self._host and self._from)

    def can_handle(self, recipient: Recipient) -> bool:
        return bool(recipient.email) and self.configured

    async def send(self, recipient: Recipient, message: Message) -> DeliveryResult:
        if not self.configured:
            return DeliveryResult(self.name, ok=False, detail="SMTP not configured")
        if not recipient.email:
            return DeliveryResult(self.name, ok=False, detail="recipient has no email")
        try:
            await asyncio.to_thread(self._send_blocking, recipient.email, message)
        except Exception as exc:  # noqa: BLE001 — transport errors become ok=False
            logger.warning("email send failed to %s: %s", recipient.email, exc)
            return DeliveryResult(self.name, ok=False, detail=str(exc))
        logger.info("invite email sent to %s", recipient.email)
        return DeliveryResult(self.name, ok=True, detail=f"sent to {recipient.email}")

    # -- blocking worker (runs in a thread) --------------------------------

    def _send_blocking(self, to_email: str, message: Message) -> None:
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = self._from
        msg["To"] = to_email
        msg.set_content(message.body_text)
        if message.body_html:
            msg.add_alternative(message.body_html, subtype="html")

        if self._use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=_TIMEOUT_S, context=ctx,
            ) as smtp:
                self._auth_and_send(smtp, msg)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=_TIMEOUT_S) as smtp:
                smtp.ehlo()
                if self._use_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                self._auth_and_send(smtp, msg)

    def _auth_and_send(self, smtp: smtplib.SMTP, msg: EmailMessage) -> None:
        if self._username and self._password:
            smtp.login(self._username, self._password)
        smtp.send_message(msg)
