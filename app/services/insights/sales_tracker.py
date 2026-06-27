"""Sales Tracker — monthly actual sales income vs a target.

Reuses the P&L income series (``compute_profitability``). Target is either
user-supplied (manual) or auto-derived as the average of the active (non-zero)
months. Pure logic: no DB/HTTP.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.insights.profitability import compute_profitability


def compute_sales_tracker(
    report: Optional[dict[str, Any]],
    target: Optional[float] = None,
) -> dict[str, Any]:
    pnl = compute_profitability(report)
    periods = pnl["periods"]
    sales = pnl["series"]["sales"]

    active = [s for s in sales if s > 0]
    auto_target = round(sum(active) / len(active), 2) if active else 0.0
    tgt = float(target) if target is not None else auto_target

    rows = []
    for period, actual in zip(periods, sales):
        variance = round(actual - tgt, 2)
        rows.append({
            "period": period,
            "actual": actual,
            "target": tgt,
            "variance": variance,
            "variance_pct": round(variance / tgt * 100, 1) if tgt else None,
            "met_target": actual >= tgt,
        })

    return {
        "periods": periods,
        "actual": sales,
        "target": tgt,
        "target_basis": "manual" if target is not None else "auto (avg of active months)",
        "total_sales": pnl["totals"]["sales"],
        "rows": rows,
    }
