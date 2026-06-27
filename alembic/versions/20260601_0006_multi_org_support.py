"""multi-org Xero support: user.nango_connection_id + company (conn,tenant) uniq

One accountant (one Nango connection) → many Company rows (one per Xero org).
- app_user.nango_connection_id: the accountant's connection, for reconcile.
- partial unique index on company(nango_connection_id, xero_tenant_id) where
  both are non-null — keeps webhook upserts idempotent without colliding on
  seed/demo rows that have NULL keys.

Revision ID: 20260601_0006
Revises: 20260601_0005
Create Date: 2026-06-01

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_0006"
down_revision: Union[str, None] = "20260601_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("nango_connection_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_app_user_nango_connection_id",
        "app_user",
        ["nango_connection_id"],
    )
    op.create_index(
        "uq_company_connection_tenant",
        "company",
        ["nango_connection_id", "xero_tenant_id"],
        unique=True,
        postgresql_where=sa.text(
            "nango_connection_id IS NOT NULL AND xero_tenant_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_company_connection_tenant", table_name="company")
    op.drop_index("ix_app_user_nango_connection_id", table_name="app_user")
    op.drop_column("app_user", "nango_connection_id")
