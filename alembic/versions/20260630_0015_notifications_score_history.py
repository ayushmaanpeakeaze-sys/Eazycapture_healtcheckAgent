"""notification feed + score_history (real health-score drop alerts)

`score_history` snapshots a company's health score per audit run so the Alerts
feed can show a REAL drop ("60% -> 2%") instead of the current number labelled
as a "drop". `notification` is a firm-scoped activity feed for team + access +
connect events (invite sent/accepted, access granted, org connected/removed).

Revision ID: 20260630_0015
Revises: 20260630_0014
Create Date: 2026-06-30

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260630_0015"
down_revision: Union[str, None] = "20260630_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "score_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("health_score", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_score_history_company_id", "score_history", ["company_id"])
    op.create_index(
        "ix_score_history_company_time", "score_history", ["company_id", "recorded_at"]
    )

    op.create_table(
        "notification",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "firm_id",
            UUID(as_uuid=True),
            sa.ForeignKey("firm.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_notification_firm_id", "notification", ["firm_id"])
    op.create_index(
        "ix_notification_firm_time", "notification", ["firm_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("notification")
    op.drop_table("score_history")
