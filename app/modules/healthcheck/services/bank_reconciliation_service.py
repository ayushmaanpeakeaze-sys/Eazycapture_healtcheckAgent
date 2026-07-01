"""Bank Reconciliation Summary — runtime orchestration.

Fetches the three Accounting-API reports (Trial Balance, Chart of Accounts,
BankTransactions), runs ``compute_bank_reconciliation_summary``, attaches a
Xero "Process" deep-link per account, and honours the per-company exclude list.

Reuses the SAME unreconciled detection as the rest of the app — the
``IsReconciled`` flag on BankTransactions — so this view is consistent with the
Unreconciled Bank Items check; it just adds the derived "Statement Balance
(calculated)" on top.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import UUID

from app.modules.healthcheck.services.company_config import CompanyConfigStore
from app.modules.healthcheck.xero_links import xero_deep_link
from app.modules.integrations.service import IntegrationService
from app.services.healthcheck.bank_reconciliation import (
    compute_bank_reconciliation_summary,
)

logger = logging.getLogger("eazycapture.bank_reconciliation_service")


def _excluded(cfg: dict[str, Any]) -> set[str]:
    # reuse the Bank Balance check's exclude list — same bank accounts
    node = cfg.get("bank_balance")
    codes = node.get("excluded") if isinstance(node, dict) else None
    return {str(c).strip().upper() for c in (codes or [])}


class BankReconciliationService:
    def __init__(self, db, integration: Optional[IntegrationService] = None) -> None:
        self._db = db
        self._store = CompanyConfigStore(db)
        self._integration = integration or IntegrationService()

    async def summary(self, company_id: UUID, *, show_all: bool = False) -> dict[str, Any]:
        company, cfg = await self._store.load(company_id)
        if company is None:
            return {"total_unreconciled_count": 0, "imported_statement_available": False, "accounts": []}
        conn = getattr(company, "nango_connection_id", None)
        tenant = getattr(company, "xero_tenant_id", None)
        shortcode = getattr(company, "xero_shortcode", None)

        # the three sources are independent — fetch them concurrently
        coa, tb, txns = await asyncio.gather(
            self._integration.fetch_chart_of_accounts(conn, tenant),
            self._integration.fetch_trial_balance(conn, tenant),
            self._load_bank_txns(company_id, conn, tenant),
        )
        coa = coa or []

        result = compute_bank_reconciliation_summary(
            tb, coa, txns, exclude_codes=_excluded(cfg),
        )

        accounts: list[dict[str, Any]] = []
        for a in result["accounts"]:
            a["process_url"] = xero_deep_link("BANK", a["account_id"], shortcode)
            # only surface accounts that need attention (unless "Show all")
            if a["needs_reconciliation"] or show_all:
                accounts.append(a)

        return {
            "total_unreconciled_count": result["total_unreconciled_count"],
            # the imported (bank-feed) balance needs Xero's gated Finance API
            "imported_statement_available": False,
            "accounts": accounts,
        }

    async def _load_bank_txns(
        self, company_id: UUID, conn: Optional[str], tenant: Optional[str],
    ) -> list[dict[str, Any]]:
        """Bank transactions for the reconciliation view.

        Under ``AUDIT_SOURCE=db`` read the SYNCED rows (same source the main
        audit + Unreconciled check use) so the view is reliable even when the
        live Xero token has died. Falls back to a live fetch when nothing has
        been synced yet.
        """
        from app.core.config import settings as _settings

        if _settings.AUDIT_SOURCE == "db":
            from sqlalchemy import select as _select

            from app.modules.integrations.sync.models import XeroDocument

            rows = (
                await self._db.execute(
                    _select(XeroDocument.raw_json).where(
                        XeroDocument.company_id == company_id,
                        XeroDocument.entity == "bank_transaction",
                    )
                )
            ).scalars().all()
            txns = [r for r in rows if isinstance(r, dict)]
            if txns:
                return txns

        if self._integration.is_connected(conn, tenant):
            return await self._integration.fetch_all_bank_transactions(conn, tenant) or []
        return []
