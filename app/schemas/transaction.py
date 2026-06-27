"""Pydantic v2 schemas exchanged between Django (EazyCapture) and the AI agent."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

IssueType = Literal[
    # High importance
    "duplicate_invoice",
    "duplicate_bill",
    "duplicate_credit_note",
    "old_unpaid_invoice",
    "old_unpaid_bill",
    "old_unsettled_sales_credit",
    "old_unsettled_purchase_credit",
    "opening_balance_difference",
    # Medium importance
    "invoice_or_direct_booking",
    "bill_or_direct_booking",
    "bill_direct_payment",       # unpaid bill + matching direct SPEND payment
    "invoice_direct_deposit",    # unpaid invoice + matching direct RECEIVE deposit

    "low_cost_fixed_asset",
    "capital_item_review",
    "wrong_category",           # displayed as "Miscategorized Items"
    "misallocated_item",        # deterministic — vague account + material amount
    "undocumented_bill",        # deterministic — supplier bill with no attachment
    "multi_account_supplier",
    "multi_tax_code_supplier",
    "unexpected_account",
    "unexpected_tax_code",
    "amount_outlier",           # deterministic — amount far off vendor's typical
    "anomaly",                  # LLM — holistic "this transaction is unusual"
    "purchase_tax_missing",
    "sales_tax_missing",
    "sales_tax_on_bills",
    "purchase_tax_on_invoices",
    "unapproved_invoice",
    "unapproved_bill",
    # Contact rules
    "duplicate_contact",
    "contact_defaults",
    "inactive_contact",
    # Other / supporting rules
    "missing_tax",
    "missing_vendor",
    "missing_invoice_number",
    "wrong_direction_account",
    "invalid_tax_code",
    "duplicate_vendor",
    "future_dated",
    "currency_mismatch",
    "invalid_status_combo",
]
Severity = Literal["critical", "high", "medium"]


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------- Pre-Ledger Firewall ----------

class InvoicePayload(_StrictBase):
    date: date
    description: str = Field(..., min_length=1, max_length=1000)
    amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    invoice_number: Optional[str] = Field(default=None, max_length=64)
    tax_code: Optional[str] = Field(default=None, max_length=32)


class InvoiceValidationResponse(_StrictBase):
    suggested_category: Optional[str] = None
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    validation_errors: list[str] = Field(default_factory=list)


# ---------- Post-Ledger Cleanup ----------

class BatchLineItem(_StrictBase):
    """One line of an invoice/bill, so per-line tax + account checks can
    examine the WHOLE document, not just line 1."""
    account_code: Optional[str] = Field(default=None, max_length=32)
    tax_code: Optional[str] = Field(default=None, max_length=32)
    amount: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    tax_amount: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    description: Optional[str] = Field(default=None, max_length=1000)


class BatchTransaction(_StrictBase):
    transaction_id: str = Field(..., min_length=1, max_length=128)
    date: date
    description: str = Field(..., min_length=1, max_length=1000)
    amount: Decimal = Field(..., max_digits=12, decimal_places=2)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    # Xero Contact.ContactID — the FOREIGN KEY. Per-contact checks group on
    # this (not vendor_name). None for seeded/legacy data.
    contact_id: Optional[str] = Field(default=None, max_length=64)
    # Xero Reference — the SUPPLIER's invoice number (optional → may be absent
    # in the API). Used for duplicate detection (NOT InvoiceNumber, which is
    # the org's own number).
    reference: Optional[str] = Field(default=None, max_length=255)
    tax_code: Optional[str] = Field(default=None, max_length=32)
    current_account_code: Optional[str] = Field(default=None, max_length=32)
    invoice_number: Optional[str] = Field(default=None, max_length=64)
    due_date: Optional[date] = None
    status: Optional[str] = Field(default=None, max_length=32)
    amount_paid: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    allocated_amount: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    amount_due: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    # Whether the document's payment is BANK MATCHED (reconciled to a bank
    # statement line in Xero). From Payments.IsReconciled. None = not fetched.
    reconciled: Optional[bool] = None
    # Whether the document has any attachment (PDF/receipt/image) in Xero —
    # from HasAttachments. None = not fetched. Drives the Undocumented-Bills check.
    has_attachments: Optional[bool] = None
    # Document-level total tax (Xero TotalTax) — for the "tax only" filter.
    tax_total: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    currency_code: Optional[str] = Field(default=None, max_length=8)
    type: Optional[str] = Field(default=None, max_length=32)
    posted_date: Optional[date] = None
    # Every line of the document (each with its own account_code + tax_code).
    # Empty for seeded/legacy data → checks fall back to the flat tax_code /
    # current_account_code fields above.
    line_items: list[BatchLineItem] = Field(default_factory=list)


class ChartOfAccount(_StrictBase):
    code: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    vat_code: Optional[str] = None
    statement: Optional[str] = None


class TaxRate(_StrictBase):
    code: Optional[str] = None
    name: Optional[str] = None
    rate: Optional[str] = None
    # Xero's authoritative direction flags (from /TaxRates): whether this code
    # may be used on expenses (bills) / revenue (sales). The wrong-direction
    # check uses these instead of guessing from the code name.
    can_apply_to_expenses: Optional[bool] = None
    can_apply_to_revenue: Optional[bool] = None


class ContactDefault(_StrictBase):
    """A contact's saved defaults (from Xero Contact). Drives the default-based
    Unexpected-Account and Unexpected-Tax checks: a posting that differs from
    the contact's own default account/tax is flagged.

    Tax codes come from Xero's ``AccountsReceivableTaxType`` (sales) /
    ``AccountsPayableTaxType`` (purchases)."""
    contact_id: str = Field(..., max_length=64)
    sales_account: Optional[str] = Field(default=None, max_length=32)
    purchase_account: Optional[str] = Field(default=None, max_length=32)
    sales_tax: Optional[str] = Field(default=None, max_length=32)
    purchase_tax: Optional[str] = Field(default=None, max_length=32)


class BatchContext(_StrictBase):
    chart_of_accounts: list[ChartOfAccount] = Field(default_factory=list)
    tax_rates: list[TaxRate] = Field(default_factory=list)
    base_currency: Optional[str] = Field(default=None, max_length=8)
    # Whether the org is VAT-registered. When explicitly False, the
    # sales/purchase tax-missing checks are skipped (no point flagging missing
    # VAT for a non-VAT business). None = unknown → checks run as before.
    org_is_vat_registered: Optional[bool] = None
    # Per-contact saved default accounts. When present, Unexpected-Account runs
    # in default-based mode (Xenon parity); when empty it falls back to the
    # frequency-outlier heuristic.
    contact_defaults: list[ContactDefault] = Field(default_factory=list)
    # Pairs of ContactIDs the Duplicate-Contacts check flagged as likely the
    # same contact. Lets duplicate detection treat them as one ledger and
    # catch invoices duplicated ACROSS the two records (Rule 4 — cross-contact).
    duplicate_contact_pairs: list[list[str]] = Field(default_factory=list)


class BatchHealthCheckRequest(_StrictBase):
    transactions: list[BatchTransaction] = Field(..., min_length=1, max_length=500)
    context: Optional[BatchContext] = None
    # When set, blocked pre-checks are persisted to the audit log as
    # kind="pre_ledger" under this company. Omit for the stateless
    # inspector use (EazyCapture batch contract) — then nothing is saved.
    company_id: Optional[str] = Field(default=None, max_length=64)
    # "pre_ledger" (default) or "preview" — the audit-log KIND these
    # persisted rows show under. Only used when company_id is set.
    kind: Optional[str] = Field(default="pre_ledger", max_length=32)
    # Per-client audit config (from the Audit Configuration screen).
    # Rule keys in ``disabled_rules`` are not run / dropped from results.
    disabled_rules: list[str] = Field(default_factory=list)
    # Transactions dated before this (YYYY-MM-DD) are skipped entirely.
    ignore_before: Optional[date] = None
    # Per-client tunable thresholds (duplicate window, overdue days, outlier
    # multiple, etc.). Keys map to ``AuditSettings`` fields; unknown keys are
    # ignored and missing keys keep defaults, so behaviour is unchanged unless
    # the client overrides a value.
    settings: Optional[dict] = None


class FlaggedIssue(_StrictBase):
    transaction_id: str
    issue_type: IssueType
    severity: Severity
    message: str
    suggested_code: Optional[str] = None
    suggested_name: Optional[str] = None
    current_code: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    # Duplicate-pair metadata (populated by duplicate_bill). Lets the
    # frontend render the two rows as a linked pair card and tell the user
    # which one is the likely original (the older of the two).
    duplicate_of_transaction_id: Optional[str] = None
    duplicate_of_invoice_number: Optional[str] = None
    duplicate_of_date: Optional[date] = None
    this_is_likely_original: Optional[bool] = None
    # "What matched" — the structured signals behind a duplicate flag, for the
    # UI chips (same_contact / same_amount / days_apart / reference_match /
    # confidence / tier). Populated by the duplicate check.
    match_reasons: Optional[dict] = None


class BatchHealthCheckResponse(_StrictBase):
    flagged: list[FlaggedIssue] = Field(default_factory=list)
