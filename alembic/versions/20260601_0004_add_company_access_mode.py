"""add app_user.company_access_mode

Two assignment modes for team members:
  "all"      → every active company, incl. future ones (flag-based)
  "selected" → only the companies in user_company_access

Revision ID: 20260601_0004
Revises: 20260601_0003
Create Date: 2026-06-01

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_0004"
down_revision: Union[str, None] = "20260601_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "company_access_mode",
            sa.String(16),
            nullable=False,
            server_default="selected",
        ),
    )


def downgrade() -> None:
    op.drop_column("app_user", "company_access_mode")
