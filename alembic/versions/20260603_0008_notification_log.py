"""add notification_log table + app_user.email_status

Production email delivery tracking: every send is logged, and the provider
webhook updates each row's status (delivered/bounced/complained). The
denormalized app_user.email_status mirrors the latest status for quick
display in the team list.

Revision ID: 20260603_0008
Revises: 20260603_0007
Create Date: 2026-06-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260603_0008"
down_revision: Union[str, None] = "20260603_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("email_status", sa.String(length=16), nullable=True),
    )
    op.create_table(
        "notification_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("recipient_email", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(length=48), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notif_recipient", "notification_log", ["recipient_email"])
    op.create_index("ix_notif_message_id", "notification_log", ["provider_message_id"])
    op.create_index("ix_notif_user", "notification_log", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notif_user", table_name="notification_log")
    op.drop_index("ix_notif_message_id", table_name="notification_log")
    op.drop_index("ix_notif_recipient", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_column("app_user", "email_status")
