"""add xero_sync_state + xero_document tables (DB-backed Xero sync)

Mirrors Xero into company-scoped tables so the audit reads from the DB instead
of re-fetching every entity live on each run. ``xero_sync_state`` holds the
per-entity incremental watermark; ``xero_document`` holds the raw Xero JSON,
one row per (company, entity, xero_id).

Revision ID: 20260628_0010
Revises: 20260615_0009
Create Date: 2026-06-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260628_0010"
down_revision: Union[str, None] = "20260615_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "xero_sync_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("watermark_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_xero_sync_state_company_id", "xero_sync_state", ["company_id"])
    op.create_index(
        "uq_sync_state_company_entity", "xero_sync_state",
        ["company_id", "entity"], unique=True,
    )

    op.create_table(
        "xero_document",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("xero_id", sa.String(length=64), nullable=False),
        sa.Column("raw_json", JSONB(), nullable=False),
        sa.Column("updated_date_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_xero_document_company_entity_xeroid", "xero_document",
        ["company_id", "entity", "xero_id"], unique=True,
    )
    op.create_index(
        "ix_xero_document_company_entity", "xero_document",
        ["company_id", "entity"],
    )


def downgrade() -> None:
    op.drop_index("ix_xero_document_company_entity", table_name="xero_document")
    op.drop_index("uq_xero_document_company_entity_xeroid", table_name="xero_document")
    op.drop_table("xero_document")
    op.drop_index("uq_sync_state_company_entity", table_name="xero_sync_state")
    op.drop_index("ix_xero_sync_state_company_id", table_name="xero_sync_state")
    op.drop_table("xero_sync_state")
