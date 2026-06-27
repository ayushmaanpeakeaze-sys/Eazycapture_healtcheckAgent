"""Deterministic health-check rules.

This file was empty on disk, which prevented the FastAPI app from importing.
The functions below provide the deterministic rule surface expected by the
orchestrator.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any, Optional

from app.schemas.transaction import BatchTransaction, FlaggedIssue
from app.services.healthcheck.audit_settings import AuditSettings
from app.services.healthcheck.shared import (
    _account_lines,
    _allowed_account_types_for_doc,
    _CREDIT_DOC_TYPES,
    _EXPENSE_ACCOUNT_TYPES,
    _MONEY_IN_TYPES,
    _MONEY_OUT_TYPES,
    _OPEN_BILL_STATUSES,
    _PURCHASE_DOC_TYPES,
    _REVENUE_ACCOUNT_TYPES,
    _SALES_DOC_TYPES,
    _UNAPPROVED_STATUSES,
    _VAGUE_ACCOUNT_NAME_KEYWORDS,
)


DEFAULT_SETTINGS = AuditSettings()


def _contact_key(tx: BatchTransaction, alias: Optional[dict[str, str]] = None) -> str:
    raw = (tx.contact_id or "").strip()
    canon = (alias or {}).get(raw, raw)
    return canon or f"name:{tx.vendor_name.strip().lower()}"


def _tax_lines(tx: BatchTransaction) -> list[tuple[Optional[int], Optional[str]]]:
    if tx.line_items:
        return [(idx + 1, item.tax_code) for idx, item in enumerate(tx.line_items)]
    return [(None, tx.tax_code)]


def _lines_with_account_and_tax(
    tx: BatchTransaction,
) -> list[tuple[Optional[int], Optional[str], Optional[Decimal], Optional[str]]]:
    """(line_no, account_code, amount, tax_code) per line — for the tax-missing
    checks, which need the account AND its tax code together on the same line."""
    if tx.line_items:
        return [
            (idx + 1, item.account_code, item.amount, item.tax_code)
            for idx, item in enumerate(tx.line_items)
        ]
    return [(None, tx.current_account_code, tx.amount, tx.tax_code)]


def _build_contact_alias(pairs: Optional[list]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    for pair in pairs or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] and pair[1]:
            left, right = find(str(pair[0])), find(str(pair[1]))
            if left != right:
                parent[max(left, right)] = min(left, right)
    return {key: find(key) for key in parent}


def _inspect_transaction(
    tx: BatchTransaction,
    allowed_tax_codes: Optional[set[str]],
    tax_codes_hint: Optional[str],
    today: date,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    issues: list[FlaggedIssue] = []
    tax_missing = False          # flag, not a line_no — a header-level (line_no=None) miss still counts
    missing_line: Optional[int] = None
    invalid_code: Optional[str] = None
    invalid_line: Optional[int] = None

    for line_no, code in _tax_lines(tx):
        clean = (code or "").strip().upper()
        if not clean and not tax_missing:
            tax_missing = True
            missing_line = line_no
        elif allowed_tax_codes is not None and clean not in allowed_tax_codes and invalid_code is None:
            invalid_code, invalid_line = code, line_no

    if tax_missing:
        where = f" (line {missing_line})" if missing_line else ""
        suffix = f" Use {tax_codes_hint}." if tax_codes_hint else " Required by Xero."
        issues.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="missing_tax",
            severity="critical",
            message=f"Tax code missing{where}.{suffix}"[:140],
        ))
    elif invalid_code is not None:
        where = f" (line {invalid_line})" if invalid_line else ""
        suffix = f" Use {tax_codes_hint}." if tax_codes_hint else ""
        issues.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="invalid_tax_code",
            severity="critical",
            message=f"Tax code {invalid_code}{where} not in this org's Xero.{suffix}"[:140],
            current_code=invalid_code,
        ))

    if not tx.vendor_name.strip():
        issues.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="missing_vendor",
            severity="critical",
            message="Vendor name missing - required by Xero.",
        ))

    if not (tx.invoice_number or "").strip():
        status = (tx.status or "").strip().upper()
        doc_type = (tx.type or "").strip().upper()
        if status in {"AUTHORISED", "PAID"} and doc_type in _SALES_DOC_TYPES:
            issue_type = "invoice_or_direct_booking"
            message = "Authorised sale has no invoice number - confirm a proper invoice was raised."
        elif status in {"AUTHORISED", "PAID"} and doc_type in _PURCHASE_DOC_TYPES:
            issue_type = "bill_or_direct_booking"
            message = "Authorised purchase has no bill reference - confirm a proper bill was raised."
        else:
            issue_type = "missing_invoice_number"
            message = "Invoice number missing - hurts reconciliation and audit trail."
        issues.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type=issue_type,
            severity="medium",
            message=message[:140],
        ))

    if tx.date > today:
        days = (tx.date - today).days
        issues.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="future_dated",
            severity="medium",
            message=f"Invoice dated {tx.date.isoformat()} - {days} day(s) in the future.",
        ))

    for check in (_check_old_unpaid, _check_invalid_status_combo, _check_unapproved, _check_old_unsettled_credit):
        issue = check(tx, today, settings) if check is not _check_invalid_status_combo else check(tx)
        if issue is not None:
            issues.append(issue)
    return issues


def _outstanding_amount(tx: BatchTransaction) -> Decimal:
    if tx.amount_due is not None:
        return tx.amount_due
    return tx.amount - ((tx.amount_paid or Decimal("0")) + (tx.allocated_amount or Decimal("0")))


def _check_old_unpaid(
    tx: BatchTransaction,
    today: date,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> Optional[FlaggedIssue]:
    doc_type = (tx.type or "").strip().upper()
    if doc_type in _CREDIT_DOC_TYPES:
        return None
    status = (tx.status or "").strip().upper()
    if status and status not in _OPEN_BILL_STATUSES:
        return None
    outstanding = _outstanding_amount(tx)
    if outstanding <= 0:
        return None
    is_sale = doc_type in _SALES_DOC_TYPES
    ref_date = (tx.due_date or tx.date) if settings.old_unpaid_age_basis == "due_date" else tx.date
    age = (today - ref_date).days
    threshold = settings.old_unpaid_invoice_days if is_sale else settings.old_unpaid_bill_days
    if age < threshold:
        return None
    currency = (tx.currency_code or "GBP").strip().upper()
    symbol = "£" if currency == "GBP" else f"{currency} "
    if settings.old_unpaid_age_basis == "due_date":
        msg = f"{tx.vendor_name} overdue by {age} days — {symbol}{outstanding:.2f} outstanding."
    else:
        msg = f"{tx.vendor_name} outstanding {symbol}{outstanding:.2f} for {age} days."
    return FlaggedIssue(
        transaction_id=tx.transaction_id,
        issue_type="old_unpaid_invoice" if is_sale else "old_unpaid_bill",
        severity="high",
        message=msg[:140],
        # Age column (computed at audit time with the configured basis, Xenon-style)
        # + outstanding so the frontend renders without any date math.
        match_reasons={
            "age_days": age,
            "age_basis": settings.old_unpaid_age_basis,
            "outstanding": f"{outstanding:.2f}",
            "currency": currency,
        },
    )


def _check_invalid_status_combo(tx: BatchTransaction) -> Optional[FlaggedIssue]:
    status = (tx.status or "").strip().upper()
    if status == "PAID" and tx.amount_due is not None and tx.amount_due > 0:
        return FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="invalid_status_combo",
            severity="high",
            message=f"Marked PAID but £{tx.amount_due:.2f} still outstanding."[:140],
            current_code=status,
        )
    if status == "AUTHORISED":
        paid = (tx.amount_paid or Decimal("0")) + (tx.allocated_amount or Decimal("0"))
        if paid > tx.amount:
            return FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="invalid_status_combo",
                severity="high",
                message=f"Overpaid - £{paid:.2f} recorded against £{tx.amount:.2f}."[:140],
                current_code=status,
            )
    return None


def _check_old_unsettled_credit(
    tx: BatchTransaction,
    today: date,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> Optional[FlaggedIssue]:
    doc_type = (tx.type or "").strip().upper()
    if doc_type not in _CREDIT_DOC_TYPES:
        return None
    outstanding = _outstanding_amount(tx)
    age = (today - tx.date).days
    # Xenon: "credit note is at least X days old" (by credit-note date) AND still
    # has unallocated/unrefunded credit (RemainingCredit > 0).
    if outstanding <= 0 or age < settings.credit_age_days:
        return None
    is_sale = doc_type == "ACCRECCREDIT"
    return FlaggedIssue(
        transaction_id=tx.transaction_id,
        issue_type="old_unsettled_sales_credit" if is_sale else "old_unsettled_purchase_credit",
        severity="high",
        message=f"{tx.vendor_name} credit has £{outstanding:.2f} unapplied for {age} days."[:140],
    )


def _check_unapproved(
    tx: BatchTransaction,
    today: date,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> Optional[FlaggedIssue]:
    status = (tx.status or "").strip().upper()
    doc_type = (tx.type or "").strip().upper()
    if status not in _UNAPPROVED_STATUSES:
        return None
    is_sale = doc_type in _SALES_DOC_TYPES
    is_purchase = doc_type in _PURCHASE_DOC_TYPES
    if not (is_sale or is_purchase):
        return None
    # Xenon: "Date of invoice is at least x days old" (by invoice date), default
    # 0 → flag every unapproved doc. Flag when age >= the configured minimum.
    age = (today - (tx.posted_date or tx.date)).days
    if age < settings.unapproved_grace_days:
        return None
    return FlaggedIssue(
        transaction_id=tx.transaction_id,
        issue_type="unapproved_invoice" if is_sale else "unapproved_bill",
        severity="medium",
        message=f"{'Sales invoice' if is_sale else 'Bill'} in {status} for {age} days - needs approval."[:140],
        current_code=status,
    )


def _is_paid(tx: BatchTransaction) -> bool:
    return (tx.status or "").strip().upper() == "PAID" or (
        tx.amount_due is not None and tx.amount_due == 0 and tx.amount > 0
    )


# Recurring re-prove: a same-(contact, amount) charge entered on its usual
# cadence is a subscription (LOW review), not a duplicate; one entered much
# closer than the cadence is a same-period double-entry (a real duplicate).
def _dominant(values: list[Any]) -> Optional[str]:
    cleaned = [str(value).strip() for value in values if value and str(value).strip()]
    return Counter(cleaned).most_common(1)[0][0] if cleaned else None


_BILL_DIRECT_AMOUNT_TOL = Decimal("0.01")


def _find_direct_settlement_mismatches(
    transactions: list[BatchTransaction],
    bank_transactions: list[BatchTransaction],
    *,
    doc_type: str,        # "ACCPAY" (bill) or "ACCREC" (invoice)
    bank_type: str,       # "SPEND" (money out) or "RECEIVE" (money in)
    issue_type: str,      # "bill_direct_payment" or "invoice_direct_deposit"
    window: int,
    doc_key: str,         # "bill" / "invoice" — match_reasons key prefix + wording
    bank_key: str,        # "payment" / "deposit"
    bank_phrase: str,     # "direct bank payment" / "direct bank deposit"
    settle_hint: str,     # "the bill may need to be marked paid." / "…invoice…"
) -> list[FlaggedIssue]:
    """Generic core for the two 'settled directly via the bank instead of against
    the open document' checks:
      • Bill or Direct Payment    — unpaid ACCPAY bill   ↔ SPEND (money out)
      • Invoice or Direct Deposit — unpaid ACCREC invoice ↔ RECEIVE (money in)

    Same contact + same amount, bank txn dated within ``window`` days AFTER the
    document. POSSIBLE mismatch (not confirmed). O(D + B): index bank txns by
    contact, one lookup per document.
    """
    bank_by_contact: dict[str, list[BatchTransaction]] = defaultdict(list)
    for bt in bank_transactions:
        if (bt.type or "").strip().upper() == bank_type:
            cid = (bt.contact_id or "").strip()
            if cid:
                bank_by_contact[cid].append(bt)
    if not bank_by_contact:
        return []

    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        if (tx.type or "").strip().upper() != doc_type:
            continue
        status = (tx.status or "").strip().upper()
        if status and status not in _OPEN_BILL_STATUSES:
            continue
        due = _outstanding_amount(tx)
        if due <= 0:
            continue
        cid = (tx.contact_id or "").strip()
        if not cid:
            continue
        for bank in bank_by_contact.get(cid, []):
            delta = (bank.date - tx.date).days
            if delta < 0 or delta > window:
                continue
            if abs(due - bank.amount) > _BILL_DIRECT_AMOUNT_TOL:
                continue
            currency = (tx.currency_code or "GBP").strip().upper()
            symbol = "£" if currency == "GBP" else f"{currency} "
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type=issue_type,
                severity="medium",
                message=(
                    f"{tx.vendor_name}: unpaid {doc_key} {symbol}{due:.2f} ({tx.date.isoformat()}) "
                    f"has a matching {bank_phrase} {symbol}{bank.amount:.2f} on "
                    f"{bank.date.isoformat()} ({delta}d later) — possible direct "
                    f"settlement; {settle_hint}"
                )[:200],
                match_reasons={
                    # --- the open DOCUMENT row (bill / invoice) ---
                    f"{doc_key}_transaction_id": tx.transaction_id,
                    f"{doc_key}_date": tx.date.isoformat(),
                    f"{doc_key}_amount": f"{tx.amount:.2f}",     # Total Value
                    "amount_due": f"{due:.2f}",                   # still-outstanding
                    f"{doc_key}_description": (tx.description or "").strip()[:200] or None,
                    # --- the matching BANK row (payment / deposit) ---
                    f"{bank_key}_transaction_id": bank.transaction_id,
                    f"{bank_key}_date": bank.date.isoformat(),
                    f"{bank_key}_amount": f"{bank.amount:.2f}",
                    f"{bank_key}_description": (bank.description or "").strip()[:200] or None,
                    # --- match meta ---
                    "days_apart": delta,
                    "currency": currency,
                },
            ))
            break   # one matching bank txn is enough to raise the flag
    return flagged


def _find_bill_direct_payments(
    transactions: list[BatchTransaction],
    bank_transactions: list[BatchTransaction],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Unpaid supplier BILL (ACCPAY) settled by a direct SPEND payment instead of
    against the bill → bill stays falsely unpaid / supplier risk of double-pay."""
    return _find_direct_settlement_mismatches(
        transactions, bank_transactions,
        doc_type="ACCPAY", bank_type="SPEND", issue_type="bill_direct_payment",
        window=settings.bill_direct_window_days, doc_key="bill", bank_key="payment",
        bank_phrase="direct bank payment",
        settle_hint="the bill may need to be marked paid.",
    )


def _find_invoice_direct_deposits(
    transactions: list[BatchTransaction],
    bank_transactions: list[BatchTransaction],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Unpaid customer INVOICE (ACCREC) settled by a direct RECEIVE deposit
    instead of against the invoice → invoice stays falsely unpaid, Accounts
    Receivable / profit overstated, customer chased for money already paid."""
    return _find_direct_settlement_mismatches(
        transactions, bank_transactions,
        doc_type="ACCREC", bank_type="RECEIVE", issue_type="invoice_direct_deposit",
        window=settings.invoice_direct_window_days, doc_key="invoice", bank_key="deposit",
        bank_phrase="direct bank deposit",
        settle_hint="the invoice may need to be marked paid.",
    )


# "No VAT" / "Outside scope" — the tax codes the tax-missing checks treat as
# MISSING. Deliberately EXCLUDES zero-rated / exempt: those are intentional 0%
# treatments with a real code, not missing tax.
# Purchase-side expense accounts that LEGITIMATELY carry no VAT — ignored so we
# don't false-flag them (matched on account NAME, plus the configurable code
# ignore-list). Wages, tax payments, depreciation, donations, etc.
_HISTORICAL_ADJUSTMENT_CODE = "840"
_HISTORICAL_ADJUSTMENT_NAME_KEYWORDS = ("historical adjustment", "opening balance")


def _find_opening_balance_differences(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    historical_code: str = _HISTORICAL_ADJUSTMENT_CODE,
) -> list[FlaggedIssue]:
    codes = {historical_code} if historical_code else set()
    codes.update(
        code.strip()
        for code, name in coa_lookup.items()
        if any(keyword in (name or "").lower() for keyword in _HISTORICAL_ADJUSTMENT_NAME_KEYWORDS)
    )
    return [
        FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="opening_balance_difference",
            severity="high",
            message=f"Posted to {tx.current_account_code} ({coa_lookup.get((tx.current_account_code or '').strip(), 'Historical Adjustment')}) - review.",
            current_code=(tx.current_account_code or "").strip(),
        )
        for tx in transactions
        if (tx.current_account_code or "").strip() in codes
    ]


