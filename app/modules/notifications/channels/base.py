"""Channel abstraction for outbound notifications.

Everything that delivers a message to a person — email today, WhatsApp /
SMS / Slack tomorrow — implements :class:`NotificationChannel`. The rest
of the app talks to channels only through this interface, so adding a new
platform is: drop a new module under ``channels/`` implementing ``send``,
then register it in :class:`~app.modules.notifications.service.NotificationService`.

The message model is deliberately channel-agnostic:

* email reads ``subject`` + ``body_html`` (falling back to ``body_text``),
* chat channels (WhatsApp/SMS) read ``body_text`` and ignore the rest.

So one :class:`Message` can be routed to any channel without rebuilding it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Recipient:
    """Who to reach. Each channel reads the field it needs and ignores the
    rest — email uses ``email``, WhatsApp/SMS will use ``phone``."""
    email: Optional[str] = None
    phone: Optional[str] = None      # E.164, reserved for WhatsApp/SMS
    name: Optional[str] = None


@dataclass(frozen=True)
class Message:
    """A channel-agnostic message. ``body_html`` is optional and only used
    by channels that render HTML (email)."""
    subject: str
    body_text: str
    body_html: Optional[str] = None


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of one send attempt. Never raises to callers — channels
    convert transport errors into ``ok=False`` so a failed notification
    never breaks the action that triggered it (e.g. creating an invite)."""
    channel: str
    ok: bool
    detail: str = ""


class NotificationChannel(ABC):
    """One delivery transport. Subclass + register to add a platform."""

    #: Stable identifier used to select the channel explicitly.
    name: str = "base"

    @abstractmethod
    async def send(self, recipient: Recipient, message: Message) -> DeliveryResult:
        """Deliver ``message`` to ``recipient``. Must not raise — return a
        ``DeliveryResult(ok=False, ...)`` on failure instead."""
        raise NotImplementedError

    def can_handle(self, recipient: Recipient) -> bool:
        """Whether this channel is configured AND has what it needs to
        reach ``recipient`` (e.g. email channel needs ``recipient.email``
        and SMTP creds). Used by the service to auto-pick a channel."""
        return True
