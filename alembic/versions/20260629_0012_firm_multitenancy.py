"""add firm (workspace) table + firm_id on app_user and company

Top-level multi-tenancy: each firm is an isolated workspace. Existing rows are
backfilled to one default firm so nothing is orphaned.

Revision ID: 20260629_0012
Revises: 20260629_0011
Create Date: 2026-06-29

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260629_0012"
down_revision: Union[str, None] = "20260629_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "firm",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.add_column("app_user", sa.Column("firm_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_app_user_firm", "app_user", "firm", ["firm_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_app_user_firm_id", "app_user", ["firm_id"])

    op.add_column("company", sa.Column("firm_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_company_firm", "company", "firm", ["firm_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_company_firm_id", "company", ["firm_id"])

    # Backfill existing data into one default firm (skip on an empty DB).
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT count(*) FROM app_user")).scalar():
        firm_id = uuid.uuid4()
        conn.execute(
            sa.text("INSERT INTO firm (id, name) VALUES (:id, :name)"),
            {"id": firm_id, "name": "Default Firm"},
        )
        conn.execute(sa.text("UPDATE app_user SET firm_id = :id WHERE firm_id IS NULL"), {"id": firm_id})
        conn.execute(sa.text("UPDATE company SET firm_id = :id WHERE firm_id IS NULL"), {"id": firm_id})


def downgrade() -> None:
    op.drop_index("ix_company_firm_id", table_name="company")
    op.drop_constraint("fk_company_firm", "company", type_="foreignkey")
    op.drop_column("company", "firm_id")
    op.drop_index("ix_app_user_firm_id", table_name="app_user")
    op.drop_constraint("fk_app_user_firm", "app_user", type_="foreignkey")
    op.drop_column("app_user", "firm_id")
    op.drop_table("firm")
