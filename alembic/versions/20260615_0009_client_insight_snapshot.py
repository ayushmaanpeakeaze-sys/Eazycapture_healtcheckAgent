"""add client_insight_snapshot table

Pre-computed Insights KPIs, one row per company, refreshed by a nightly Celery
task. The API serves this instantly (no live Xero on the request path) and the
firm-summary rolls up across rows.

Revision ID: 20260615_0009
Revises: 20260603_0008
Create Date: 2026-06-15

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260615_0009"
down_revision: Union[str, None] = "20260603_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_insight_snapshot",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("net_profit", sa.Numeric(16, 2), nullable=True),
        sa.Column("tax_estimate", sa.Numeric(16, 2), nullable=True),
        sa.Column("cash", sa.Numeric(16, 2), nullable=True),
        sa.Column("cash_coverage", sa.Numeric(10, 2), nullable=True),
        sa.Column("working_capital", sa.Numeric(16, 2), nullable=True),
        sa.Column("working_capital_healthy", sa.Boolean(), nullable=True),
        sa.Column("distributable_reserves", sa.Numeric(16, 2), nullable=True),
        sa.Column("net_asset_value", sa.Numeric(16, 2), nullable=True),
        sa.Column("dla_detected", sa.Boolean(), nullable=True),
        sa.Column("dla_overdrawn", sa.Boolean(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("uq_cis_company", "client_insight_snapshot", ["company_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_cis_company", table_name="client_insight_snapshot")
    op.drop_table("client_insight_snapshot")
