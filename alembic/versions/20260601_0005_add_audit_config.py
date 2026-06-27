"""add company.audit_config

Per-client audit configuration (which checks run + an optional
"ignore transactions before" date). Set on the frontend's Audit
Configuration screen.

Revision ID: 20260601_0005
Revises: 20260601_0004
Create Date: 2026-06-01

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260601_0005"
down_revision: Union[str, None] = "20260601_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company",
        sa.Column("audit_config", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company", "audit_config")
