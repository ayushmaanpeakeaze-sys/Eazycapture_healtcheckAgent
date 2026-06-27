"""Companies House integration — powers the Opening Balance Differences check.

The check compares a UK Limited Company's *filed* statutory accounts (the
Net Assets/Liabilities figure on record at Companies House) against the same
figure in the Xero bookkeeping at that period-end date.

Companies House is a **free, public** API (unlike Xero's gated Finance API):
``client`` fetches the filing history + the iXBRL accounts document, and
``ixbrl`` extracts the Net Assets figure from that document. ``service``
ties them together into ``{period_end: net_assets}``.
"""
from app.modules.integrations.companies_house.ixbrl import extract_net_assets
from app.modules.integrations.companies_house.service import (
    CompaniesHouseService,
    FiledNetAssets,
)

__all__ = ["extract_net_assets", "CompaniesHouseService", "FiledNetAssets"]
