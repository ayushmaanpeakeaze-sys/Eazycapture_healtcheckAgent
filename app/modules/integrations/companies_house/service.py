"""Companies House service — turn a company registration number into a list
of *filed* Net Assets figures, one per accounts period end.

Flow per accounts filing:
    filing-history  →  made_up_date + document_metadata link
    document content (iXBRL)  →  extract_net_assets()  →  {period_end: £}

The iXBRL of one filing usually carries both the current and prior year; we
key the figure off the filing's ``made_up_date`` so each period end resolves
to the accounts that were actually filed for it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.modules.integrations.companies_house.client import CompaniesHouseClient
from app.modules.integrations.companies_house.ixbrl import extract_net_assets

logger = logging.getLogger("eazycapture.companies_house.service")


@dataclass(frozen=True)
class FiledNetAssets:
    """Net Assets/(Liabilities) as filed at Companies House for one period."""
    period_end: str            # YYYY-MM-DD
    net_assets: Decimal
    source: str = "companies_house"   # or "manual"
    document_url: Optional[str] = None


def _made_up_date(filing: dict[str, Any]) -> Optional[str]:
    dv = filing.get("description_values") or {}
    return (
        dv.get("made_up_date")
        or filing.get("action_date")
        or filing.get("date")
    )


def _document_link(filing: dict[str, Any]) -> Optional[str]:
    links = filing.get("links") or {}
    return links.get("document_metadata")


class CompaniesHouseService:
    def __init__(self, client: Optional[CompaniesHouseClient] = None) -> None:
        self._client = client or CompaniesHouseClient()

    def is_enabled(self) -> bool:
        return self._client.is_enabled()

    async def fetch_filed_net_assets(
        self, company_number: str, *, max_filings: int = 6,
    ) -> list[FiledNetAssets]:
        """Return filed Net Assets per period end (latest filing wins on
        duplicate period ends). Empty list if CH is disabled, the company is
        unknown, or no accounts filing is machine-readable (PDF-only)."""
        if not self.is_enabled():
            return []
        history = await self._client.get_filing_history(company_number)
        if not history:
            return []
        items = [
            f for f in (history.get("items") or [])
            if (f.get("category") == "accounts") and _document_link(f) and _made_up_date(f)
        ]
        # Newest first so a later refiling overrides an earlier one.
        items.sort(key=_made_up_date, reverse=True)

        by_period: dict[str, FiledNetAssets] = {}
        for filing in items[:max_filings]:
            period = _made_up_date(filing)
            if period in by_period:
                continue
            doc_url = _document_link(filing)
            content = await self._client.get_document_content(doc_url)
            if not content:
                logger.info("CH %s: no iXBRL content for period %s", company_number, period)
                continue
            parsed = extract_net_assets(content)
            value = parsed.get(period)
            if value is None and len(parsed) == 1:
                # Single-period filing whose context date drifts a day from
                # made_up_date — trust the one figure present.
                value = next(iter(parsed.values()))
            if value is None:
                logger.info("CH %s: no Net Assets tag for period %s", company_number, period)
                continue
            by_period[period] = FiledNetAssets(
                period_end=period, net_assets=value,
                source="companies_house", document_url=doc_url,
            )

        return sorted(by_period.values(), key=lambda f: f.period_end, reverse=True)
