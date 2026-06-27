"""Auth / RBAC ORM models — the identity tables owned by this module.

``UserCompanyAccess`` references ``company`` and ``app_user`` purely by
table-name strings in the ForeignKeys, so this module does NOT import the
healthcheck models — keeping the modules decoupled. Cross-table relationship
targets are resolved by SQLAlchemy's registry at mapper-configure time,
which requires every model module to be imported once at startup (the app
routers and alembic/env.py both do this).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, uuid_pk


class User(Base):
    """An app user. Two roles only:

    * ``admin``        — full access to every company; can invite team
                         members and assign them companies.
    * ``team_member``  — invite-only; can only access the companies an
                         admin has assigned via :class:`UserCompanyAccess`.

    ``status`` is ``invited`` until the user accepts the invite and sets
    a password, then ``active``. ``password_hash`` is null while invited.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        Index("ix_app_user_email", "email", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # admin | team_member
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="team_member")
    # invited | active | disabled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="invited")
    # Company access mode for team members:
    #   "all"      → every active company, including future ones (flag-based)
    #   "selected" → only the companies in user_company_access (default)
    # Ignored for admins (they always have all).
    company_access_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="selected",
    )
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The accountant's Nango connection (set when they connect Xero). One
    # connection fans out to many Company rows (one per Xero org). Used by
    # the daily reconcile to re-enumerate the orgs this user can access.
    nango_connection_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, index=True,
    )
    # One-time invite token (cleared once accepted).
    invite_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    invite_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    # Delivery status of the most recent email we sent this user, updated by
    # the email-provider webhook: sent | delivered | bounced | complained |
    # failed. None = nothing recorded yet. Lets the team list flag a bad
    # address (e.g. an invite that bounced) without joining notification_log.
    email_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    company_access: Mapped[list["UserCompanyAccess"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserCompanyAccess(Base):
    """Many-to-many: which companies a team member can work on.

    Admins ignore this table entirely (they see every company). Only
    team members are gated by the rows here. The ``company_id`` FK points
    at the healthcheck-owned ``company`` table by name — no import needed.
    """

    __tablename__ = "user_company_access"
    __table_args__ = (
        Index("ix_uca_user_company", "user_id", "company_id", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="company_access")


__all__ = ["User", "UserCompanyAccess"]
