"""add bank_note + bank_document tables (Bank Balance Check review annotations)

Internal EazyCapture review data for the Bank Balance Check — notes the
accountant writes and supporting files they upload, both keyed to one bank
account at one period end. Never sent to Xero.

Revision ID: 20260629_0011
Revises: 20260628_0010
Create Date: 2026-06-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260629_0011"
down_revision: Union[str, None] = "20260628_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_note",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("account_code", sa.String(length=32), nullable=False),
        sa.Column("period_end", sa.String(length=16), nullable=False),
        sa.Column("author_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tagged_user_ids", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_note_company_id", "bank_note", ["company_id"])
    op.create_index("ix_bank_note_lookup", "bank_note", ["company_id", "account_code", "period_end"])

    op.create_table(
        "bank_document",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("account_code", sa.String(length=32), nullable=False),
        sa.Column("period_end", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bank_document_company_id", "bank_document", ["company_id"])
    op.create_index("ix_bank_document_lookup", "bank_document", ["company_id", "account_code", "period_end"])


def downgrade() -> None:
    op.drop_index("ix_bank_document_lookup", table_name="bank_document")
    op.drop_index("ix_bank_document_company_id", table_name="bank_document")
    op.drop_table("bank_document")
    op.drop_index("ix_bank_note_lookup", table_name="bank_note")
    op.drop_index("ix_bank_note_company_id", table_name="bank_note")
    op.drop_table("bank_note")
