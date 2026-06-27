"""add RBAC tables: app_user + user_company_access

Two roles only — admin and team_member. Team members are invite-only
and gated to the companies assigned in user_company_access. Admins
ignore that table (full access).

Revision ID: 20260601_0003
Revises: 20260528_0002
Create Date: 2026-06-01

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260601_0003"
down_revision: Union[str, None] = "20260528_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="team_member"),
        sa.Column("status", sa.String(32), nullable=False, server_default="invited"),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("invite_token", sa.Text(), nullable=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_app_user_email", "app_user", ["email"], unique=True)
    op.create_index("ix_app_user_invite_token", "app_user", ["invite_token"])

    op.create_table(
        "user_company_access",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_user_company_access_user_id", "user_company_access", ["user_id"])
    op.create_index("ix_user_company_access_company_id", "user_company_access", ["company_id"])
    op.create_index(
        "ix_uca_user_company",
        "user_company_access",
        ["user_id", "company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("user_company_access")
    op.drop_index("ix_app_user_invite_token", table_name="app_user")
    op.drop_index("ix_app_user_email", table_name="app_user")
    op.drop_table("app_user")
