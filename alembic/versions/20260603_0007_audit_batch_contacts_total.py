"""add audit_batch.contacts_total

Stores how many contacts a run audited, so the blended health score
(documents + contacts) has a contact denominator.

Revision ID: 20260603_0007
Revises: 20260601_0006
Create Date: 2026-06-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_0007"
down_revision: Union[str, None] = "20260601_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_batch",
        sa.Column("contacts_total", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("audit_batch", "contacts_total")
