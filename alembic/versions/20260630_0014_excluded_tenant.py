"""excluded_tenant — orgs a firm removed, so the connect webhook won't re-add them

The Xero OAuth grant is connection-level: it covers every org the user can
reach. The auth.creation webhook enumerates them all, so deleting/disconnecting
an org in-app was undone the moment the user connected any new org (the grant
re-enumerated and resurrected it). This table records the orgs a firm has
explicitly removed; the webhook skips them. Removing a row re-allows the org.

Revision ID: 20260630_0014
Revises: 20260630_0013
Create Date: 2026-06-30

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260630_0014"
down_revision: Union[str, None] = "20260630_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "excluded_tenant",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "firm_id",
            UUID(as_uuid=True),
            sa.ForeignKey("firm.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("xero_tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_excluded_tenant_firm_id", "excluded_tenant", ["firm_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_excluded_tenant_firm
        ON excluded_tenant (firm_id, xero_tenant_id)
        WHERE firm_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("excluded_tenant")
