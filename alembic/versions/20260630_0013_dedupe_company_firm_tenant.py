"""dedupe companies per (firm, tenant) + swap the org-uniqueness index

A Xero org belongs to exactly one firm, but the old uniqueness index keyed on
(nango_connection_id, xero_tenant_id) — so a RECONNECT (fresh connection_id)
created a duplicate company row for the same org. Re-key the guarantee on
(firm_id, xero_tenant_id) and remove any duplicates that the old behaviour left
behind (keeping the most recently connected row; child rows cascade).

Revision ID: 20260630_0013
Revises: 20260629_0012
Create Date: 2026-06-30

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260630_0013"
down_revision: Union[str, None] = "20260629_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Remove existing within-firm duplicate orgs — keep the most recently
    #    created row per (firm_id, tenant_id); ON DELETE CASCADE clears its data.
    op.execute(
        """
        DELETE FROM company c
        WHERE c.firm_id IS NOT NULL AND c.xero_tenant_id IS NOT NULL
          AND c.id NOT IN (
            SELECT DISTINCT ON (firm_id, xero_tenant_id) id
            FROM company
            WHERE firm_id IS NOT NULL AND xero_tenant_id IS NOT NULL
            ORDER BY firm_id, xero_tenant_id, created_at DESC, id DESC
          )
        """
    )
    # 2. Swap the uniqueness guarantee: connection+tenant → firm+tenant.
    op.execute("DROP INDEX IF EXISTS uq_company_connection_tenant")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_company_firm_tenant
        ON company (firm_id, xero_tenant_id)
        WHERE firm_id IS NOT NULL AND xero_tenant_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_company_firm_tenant")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_company_connection_tenant
        ON company (nango_connection_id, xero_tenant_id)
        WHERE nango_connection_id IS NOT NULL AND xero_tenant_id IS NOT NULL
        """
    )
