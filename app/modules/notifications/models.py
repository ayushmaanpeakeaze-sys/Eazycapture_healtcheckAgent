"""Notifications ORM models — owned by this module.

``NotificationLog`` links to ``app_user`` purely via a table-name FK
string, so this module does not import the auth models. It deliberately
has no ORM ``relationship`` to User (looser coupling) — the link is keyed
by ``user_id`` and resolved with explicit queries where needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, uuid_pk


class NotificationLog(Base):
    """Audit + delivery-tracking record for every outbound notification.

    One row per send attempt (email today; WhatsApp/SMS later). ``status``
    starts at ``sent``/``failed`` from the send call, then the provider
    webhook upgrades it to ``delivered`` or downgrades it to
    ``bounced``/``complained``. This is the durable history; ``User.email_status``
    is the denormalized 'latest' for quick display.
    """

    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notif_recipient", "recipient_email"),
        Index("ix_notif_message_id", "provider_message_id"),
        Index("ix_notif_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Keep the log even if the user is removed → SET NULL, not CASCADE.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipient_email: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)   # email | whatsapp | ...
    kind: Mapped[str] = mapped_column(String(48), nullable=False)      # invite | resend_invite | ...
    # queued | sent | delivered | bounced | complained | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    provider: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )


__all__ = ["NotificationLog"]
