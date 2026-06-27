"""initial schema — company, invoice, invoice_line_item, health_check_result, audit_batch

Revision ID: 20260527_0001
Revises:
Create Date: 2026-05-27

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260527_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("xero_tenant_id", sa.Text(), nullable=True),
        sa.Column("nango_connection_id", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "invoice",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("invoice_number", sa.Text(), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount_due", sa.Numeric(12, 2), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("tax_code", sa.String(32), nullable=True),
        sa.Column("account_code", sa.String(32), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column(
            "currency_code", sa.String(8), nullable=False, server_default="GBP",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_invoice_company_id", "invoice", ["company_id"])
    op.create_index(
        "ix_invoice_company_status", "invoice", ["company_id", "status"],
    )

    op.create_table(
        "invoice_line_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoice.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "quantity", sa.Numeric(12, 4), nullable=False, server_default="1",
        ),
        sa.Column("unit_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("account_code", sa.String(32), nullable=True),
        sa.Column("tax_type", sa.String(32), nullable=True),
        sa.Column("line_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.create_index(
        "ix_invoice_line_item_invoice_id", "invoice_line_item", ["invoice_id"],
    )

    op.create_table(
        "health_check_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_msgs", sa.Text(), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_hcr_company_id", "health_check_result", ["company_id"])
    op.create_index("ix_hcr_document_id", "health_check_result", ["document_id"])
    op.create_index("ix_hcr_ran_at", "health_check_result", ["ran_at"])
    op.create_index(
        "ix_hcr_company_ran_at", "health_check_result", ["company_id", "ran_at"],
    )
    op.create_index(
        "ix_hcr_document_ran_at", "health_check_result", ["document_id", "ran_at"],
    )

    op.create_table(
        "audit_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_trapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "audit_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "ai_enriched_count", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column(
            "ai_enrichment_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_audit_batch_company_id", "audit_batch", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_batch_company_id", table_name="audit_batch")
    op.drop_table("audit_batch")

    op.drop_index("ix_hcr_document_ran_at", table_name="health_check_result")
    op.drop_index("ix_hcr_company_ran_at", table_name="health_check_result")
    op.drop_index("ix_hcr_ran_at", table_name="health_check_result")
    op.drop_index("ix_hcr_document_id", table_name="health_check_result")
    op.drop_index("ix_hcr_company_id", table_name="health_check_result")
    op.drop_table("health_check_result")

    op.drop_index("ix_invoice_line_item_invoice_id", table_name="invoice_line_item")
    op.drop_table("invoice_line_item")

    op.drop_index("ix_invoice_company_status", table_name="invoice")
    op.drop_index("ix_invoice_company_id", table_name="invoice")
    op.drop_table("invoice")

    op.drop_table("company")
