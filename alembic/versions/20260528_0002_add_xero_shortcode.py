"""add company.xero_shortcode

Used in the org-scoped Xero deep-link URL pattern
``https://go.xero.com/app/{shortcode}/invoicing/view/{id}`` so the
"Open in Xero" button always lands in the right tenant, even when the
user has multiple Xero orgs in their browser session.

Revision ID: 20260528_0002
Revises: 20260527_0001
Create Date: 2026-05-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0002"
down_revision: Union[str, None] = "20260527_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company",
        sa.Column("xero_shortcode", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company", "xero_shortcode")
