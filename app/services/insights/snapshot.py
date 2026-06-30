"""Compute a full Insights snapshot for one company.

Fetches the three Xero reports (P&L, Balance Sheet, Trial Balance) and runs all
the KPI calculators. Returns a dict with flat summary fields (for the firm
rollup) + a ``payload`` holding the full per-KPI data (for instant per-org
serve). Network I/O lives here; persistence is the caller's job.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from app.modules.integrations.service import IntegrationService
from app.services.insights.balance_sheet import compute_financial_position
from app.services.insights.bank import compute_bank_balance, compute_bank_reconciliation
from app.services.insights.corp_tax import estimate_corporation_tax
from app.services.insights.directors_loans import find_director_loans
from app.services.insights.profitability import compute_profitability
from app.services.insights.sales_tracker import compute_sales_tracker


async def compute_company_snapshot(
    connection_id: str,
    tenant_id: str,
    periods: int = 11,
    sales_target_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    integ = IntegrationService()
    pnl, bs, tb, bank_txns, bank_summary = await asyncio.gather(
        integ.fetch_profit_and_loss(connection_id, tenant_id, periods=periods),
        integ.fetch_balance_sheet(connection_id, tenant_id),
        integ.fetch_trial_balance(connection_id, tenant_id),
        integ.fetch_all_bank_transactions(connection_id, tenant_id),
        integ.fetch_bank_summary(connection_id, tenant_id),
    )

    # A failed report fetch (Xero connection needs re-auth, transient proxy
    # error) returns None. Computing on that would silently produce an all-zero
    # snapshot and overwrite the last good one. Treat a missing P&L — the core
    # report behind profit + sales — as a failure so the caller keeps the
    # previous snapshot (marked stale) instead of replacing it with zeros.
    if pnl is None:
        raise RuntimeError(
            "Xero ProfitAndLoss report unavailable — connection may need re-auth"
        )

    profitability = compute_profitability(pnl)
    sales_tracker = compute_sales_tracker(pnl, config_raw=sales_target_config)
    position = compute_financial_position(bs)
    net_profit = profitability["totals"]["net_profit"]
    corp_tax = estimate_corporation_tax(net_profit)
    dla = find_director_loans(tb)
    bank = compute_bank_reconciliation(bank_txns)
    bank_balance = compute_bank_balance(tb, bank_summary, bank_txns)

    return {
        # summary (firm rollup)
        "net_profit": net_profit,
        "tax_estimate": corp_tax["tax_estimate"],
        "cash": position["position"]["cash"],
        "cash_coverage": position["cash_health"]["coverage_ratio"],
        "working_capital": position["working_capital"]["working_capital"],
        "working_capital_healthy": position["working_capital"]["healthy"],
        "distributable_reserves": position["dividend"]["distributable_reserves"],
        "net_asset_value": position["valuation"]["net_asset_value"],
        "dla_detected": dla["detected"],
        "dla_overdrawn": any(a.get("overdrawn") for a in dla["accounts"]),
        # full payload (per-org serve)
        "payload": {
            "profitability": profitability,
            "sales_tracker": sales_tracker,
            "financial_position": position,
            "corporation_tax": {
                "period_basis": "trailing 12 months",
                **corp_tax,
            },
            "directors_loans": dla,
            "bank_reconciliation": bank,
            "bank_balance": bank_balance,
        },
    }
