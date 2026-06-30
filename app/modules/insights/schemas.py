"""Pydantic response models for the Insights endpoints — matches the rest of
the project's convention (typed responses → validation + clean OpenAPI docs the
frontend consumes)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Profitability ---------------------------------------------------------

class ProfitabilitySeries(BaseModel):
    sales: list[float]
    gross_profit: list[float]
    net_profit: list[float]


class KpiTotals(BaseModel):
    sales: float
    gross_profit: float
    net_profit: float


class ProfitabilityResponse(BaseModel):
    company_id: str
    kpi: str
    report_name: Optional[str] = None
    report_date: Optional[str] = None
    periods: list[str]
    series: ProfitabilitySeries
    totals: KpiTotals


# --- Sales Tracker ---------------------------------------------------------

class SalesTrackerRow(BaseModel):
    period: str
    actual: float
    target: float
    variance: float
    variance_pct: Optional[float] = None
    met_target: bool


class SalesTrackerResponse(BaseModel):
    company_id: str
    kpi: str
    periods: list[str]
    actual: list[float]
    target: float
    target_basis: str
    total_sales: float
    rows: list[SalesTrackerRow]


# --- Financial position (Balance Sheet — 5 KPIs in one) --------------------

class PositionBlock(BaseModel):
    total_assets: float
    total_liabilities: float
    net_assets: float
    cash: float
    current_assets: float
    fixed_assets: float
    current_liabilities: float


class CashHealthBlock(BaseModel):
    cash: float
    current_liabilities: float
    coverage_ratio: Optional[float] = None
    shortfall: float


class WorkingCapitalBlock(BaseModel):
    current_assets: float
    current_liabilities: float
    working_capital: float
    current_ratio: Optional[float] = None
    healthy: bool


class DividendBlock(BaseModel):
    retained_earnings: float
    current_year_earnings: float
    distributable_reserves: float
    basis: str


class ValuationBlock(BaseModel):
    model: str
    net_asset_value: float


class FinancialPositionResponse(BaseModel):
    company_id: str
    kpi: str
    report_date: Optional[str] = None
    position: PositionBlock
    cash_health: CashHealthBlock
    working_capital: WorkingCapitalBlock
    dividend: DividendBlock
    valuation: ValuationBlock


# --- Corporation Tax (estimate) --------------------------------------------

class CorporationTaxResponse(BaseModel):
    company_id: str
    kpi: str
    period_basis: str
    taxable_profit: float
    tax_estimate: float
    band: str
    effective_rate: float
    note: str


# --- Directors' Loan Accounts ----------------------------------------------

class DirectorLoanAccount(BaseModel):
    account: str
    code: Optional[str] = None
    balance: float
    overdrawn: bool
    note: str


class DirectorsLoanResponse(BaseModel):
    company_id: str
    kpi: str
    detected: bool
    accounts: list[DirectorLoanAccount]
    note: str


# --- Snapshot serve + firm rollup ------------------------------------------

class SnapshotResponse(BaseModel):
    company_id: str
    computed_at: Optional[str] = None
    status: str
    stale: bool                       # True if the snapshot is missing/old
    refreshing: bool = False          # True while a manual refresh is recomputing
    payload: dict                     # full per-KPI data (see per-KPI schemas)


class RefreshResponse(BaseModel):
    company_id: str
    status: str                       # "queued" | "already_refreshing"
    refreshing: bool = True


class SalesTargetConfigModel(BaseModel):
    # one of: none | previous_month | average_3 | average_6 | average_12 |
    #         same_month_last_year | xero_budget | manual
    basis: str = "average_3"
    adjustment_pct: float = 0.0       # +/- nudge applied to the derived target
    manual_value: Optional[float] = None  # only for basis == "manual"


class CashHealthSettingsModel(BaseModel):
    """Cash Health Check settings (the Settings cog). Categories:
    suppliers | net_wages | paye_nic | pension | credit_cards | vat |
    corporation_tax | loans_other."""
    # category -> include this outgoing in the totals (default: all included)
    included: dict[str, bool] = Field(default_factory=dict)
    # category -> manual override value (replaces the auto Xero figure)
    overrides: dict[str, float] = Field(default_factory=dict)
    # account code -> category (re-assign a nominal account)
    account_overrides: dict[str, str] = Field(default_factory=dict)
    # bank account codes to leave OUT of the current-cash figure
    disregarded_banks: list[str] = Field(default_factory=list)


class FirmClientRow(BaseModel):
    company_id: str
    name: str
    computed_at: Optional[str] = None
    net_profit: Optional[float] = None
    working_capital: Optional[float] = None
    cash_coverage: Optional[float] = None
    dla_overdrawn: Optional[bool] = None
    unreconciled_bank_items: Optional[int] = None
    last_bank_reconciled: Optional[str] = None
    most_recent_transaction: Optional[str] = None


class FirmTotals(BaseModel):
    total_clients: int
    with_snapshot: int
    in_profit: int
    in_loss: int
    cash_tight: int                   # coverage_ratio < 0.2
    working_capital_negative: int
    dla_overdrawn: int
    unreconciled_bank_items: int      # total across all clients


class FirmSummaryResponse(BaseModel):
    totals: FirmTotals                # firm-level counts (the tiles)
    clients: list[FirmClientRow]      # per-client rows (the table)
