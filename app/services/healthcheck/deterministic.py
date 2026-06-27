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


def _account_lines(
    tx: BatchTransaction,
) -> list[tuple[Optional[int], Optional[str], Optional[Decimal]]]:
    if tx.line_items:
        return [
            (idx + 1, item.account_code, item.amount)
            for idx, item in enumerate(tx.line_items)
        ]
    return [(None, tx.current_account_code, tx.amount)]


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
_RECUR_MIN_SERIES = 3      # need >= 3 in the series to learn a cadence
_RECUR_PAIR_GAP = 14       # lone pair (no series) this far apart → recurring review
_RECUR_REVIEW = 0.45       # capped confidence for a recurring pair


def _dup_issue_type(doc_type: str) -> str:
    if doc_type in _CREDIT_DOC_TYPES:
        return "duplicate_credit_note"
    if doc_type in _SALES_DOC_TYPES:
        return "duplicate_invoice"
    return "duplicate_bill"


def _ref_match(a: BatchTransaction, b: BatchTransaction) -> str:
    """RAW (case-sensitive, no normalization) reference compare → exact | none |
    different. ``none`` when either side has no reference."""
    ra, rb = (a.reference or "").strip(), (b.reference or "").strip()
    if not ra or not rb:
        return "none"
    return "exact" if ra == rb else "different"


def _find_duplicate_bills(
    transactions: list[BatchTransaction],
    contact_alias: Optional[dict[str, str]] = None,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Per-contact, additive/rule-based duplicate detection.

    Blocks by the REAL Xero ContactID AND document family, scores each in-window
    pair on a rule waterfall (same invoice number → 1.0; recurring → 0.45;
    different amount → 0.65; different numbers same/other day → 0.95/0.70; exact
    reference → 0.90; else 0.75), and only marks ORIGINAL vs DUPLICATE for
    HIGH-tier (confirmed) pairs — weaker review pairs get just a confidence score.

    NOTE: duplicate invoices key on the actual ContactID only. Two distinct
    ContactIDs are ALWAYS treated as separate parties — the duplicate-contacts
    alias is deliberately NOT applied here, so a fuzzy contact guess can never
    manufacture a cross-contact "duplicate". ``contact_alias`` is accepted for
    call-site compatibility but intentionally ignored.
    """
    # Block by (real ContactID, document family) — never pair a sale with a bill,
    # an invoice with a credit note, or two DIFFERENT contact records. Unlike the
    # other checks we do NOT canonicalise through the contact alias here: only
    # Xero's own ContactID groups invoices together.
    by_group: dict[tuple[str, str], list[BatchTransaction]] = defaultdict(list)
    for tx in transactions:
        doc_type = (tx.type or "").strip().upper()
        if not doc_type:
            continue
        by_group[(_contact_key(tx), doc_type)].append(tx)

    flagged: list[FlaggedIssue] = []
    for (contact_key, doc_type), group in by_group.items():
        if len(group) < 2:
            continue

        # Learn the cadence of each same-amount series (>= 3) so a recurring
        # monthly charge is told apart from a same-period double entry.
        cadence: dict[Any, float] = {}
        amount_groups: dict[Any, list[BatchTransaction]] = defaultdict(list)
        for tx in group:
            amount_groups[tx.amount].append(tx)
        for amt, txs in amount_groups.items():
            if len(txs) >= _RECUR_MIN_SERIES:
                ds = sorted(t.date for t in txs)
                gaps = [g for g in ((ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)) if g > 0]
                if gaps:
                    cadence[amt] = median(gaps)

        is_credit_pair = doc_type in _CREDIT_DOC_TYPES
        is_purchase = doc_type in _PURCHASE_DOC_TYPES
        issue_type = _dup_issue_type(doc_type)

        # Sorted sliding-window blocking: only compare pairs within the date
        # window (near-linear; identical results to all-pairs).
        group.sort(key=lambda t: t.date)
        for idx, a in enumerate(group):
            for b in group[idx + 1:]:
                days_apart = (b.date - a.date).days
                if days_apart > settings.duplicate_days_window:
                    break  # sorted → every later b is further still

                # --- gates (candidate selection) --------------------------
                same_amount = a.amount == b.amount
                if settings.duplicate_require_same_amount and not same_amount:
                    continue
                num_a, num_b = (a.invoice_number or "").strip(), (b.invoice_number or "").strip()
                same_invoice_number = bool(num_a and num_b and num_a == num_b)
                ref_match = _ref_match(a, b)
                ra, rb = (a.reference or "").strip(), (b.reference or "").strip()
                same_reference = bool(ra and rb and ra == rb)
                # The document's IDENTIFYING number: the Xero invoice number for
                # SALES; the supplier REFERENCE for PURCHASES (bills carry no
                # invoice number — the reference IS the bill number). ``diff_id``
                # = "two different documents" → the 0.95 same-day / 0.70 gap case.
                if is_purchase:
                    diff_id = bool(ra and rb and ra != rb)
                else:
                    diff_id = bool(num_a and num_b and num_a != num_b)
                # Reference gate: only SALES drop on a conflicting secondary
                # reference. Bills use the reference AS their number (scored in the
                # waterfall below), so the gate must not drop different-ref bills.
                if (settings.duplicate_require_exact_reference and not is_purchase
                        and ref_match == "different" and not same_invoice_number):
                    continue
                if (not settings.duplicate_also_check_paid
                        and _is_paid(a) and _is_paid(b) and not is_credit_pair):
                    continue

                # --- recurring re-prove -----------------------------------
                recurring = False
                if same_amount and not same_invoice_number:
                    cad = cadence.get(a.amount)
                    recurring = (days_apart >= cad * 0.75) if cad is not None \
                        else (days_apart >= _RECUR_PAIR_GAP)

                # --- confidence + tier (rule waterfall) -------------------
                # Strongest signal = same identifying number. For SALES that's the
                # (unique) invoice number → 1.0 even across dates. For BILLS it's
                # the supplier reference → 1.0, BUT only after the recurring check
                # (subscriptions reuse the same reference each period).
                if same_invoice_number:
                    confidence, tier = 1.0, "high"
                elif recurring:
                    confidence, tier = _RECUR_REVIEW, "low"
                elif not same_amount:
                    confidence, tier = 0.65, "medium"
                elif is_purchase and same_reference:
                    confidence, tier = 1.0, "high"      # bill: same supplier reference = same bill
                elif diff_id:
                    confidence, tier = (0.95, "high") if days_apart == 0 else (0.70, "medium")
                elif ref_match == "exact":
                    confidence, tier = 0.90, "high"     # sales: same PO reference, no invoice numbers
                else:
                    confidence, tier = 0.75, "medium"

                if confidence < settings.duplicate_min_confidence:
                    continue

                # "Could be 2 distinct documents" only makes sense when the
                # identifying numbers/references DIFFER AND there's a DATE GAP.
                # Same-day different numbers is just a re-entry (confident
                # duplicate), so no note there. Also suppressed for recurring.
                distinct_docs_possible = diff_id and days_apart > 0 and not recurring
                # Grouped by the real ContactID, so a pair always shares one
                # contact — cross-contact pairing is no longer possible by design.
                cross_contact = False
                one_paid_one_out = _is_paid(a) != _is_paid(b)
                risk = "high" if (tier in ("high", "medium") and one_paid_one_out) else "normal"

                # --- original vs duplicate: paid → posted_date → issue date
                a_paid, b_paid = _is_paid(a), _is_paid(b)
                if a_paid != b_paid:
                    original, duplicate = (a, b) if a_paid else (b, a)
                elif a.posted_date and b.posted_date and a.posted_date != b.posted_date:
                    original, duplicate = (a, b) if a.posted_date <= b.posted_date else (b, a)
                else:
                    original, duplicate = (a, b) if a.date <= b.date else (b, a)

                currency = (a.currency_code or "GBP").strip().upper()
                symbol = "£" if currency == "GBP" else f"{currency} "

                # "why it matched" basis for the message
                parts: list[str] = []
                if same_invoice_number:
                    parts.append(f"same invoice no. {num_a}")
                elif ref_match == "exact":
                    parts.append(f"same reference '{(a.reference or '').strip()}'")
                parts.append("same amount" if same_amount else "different amount")
                parts.append("same day" if days_apart == 0 else f"{days_apart} day(s) apart")
                basis = ", ".join(parts)

                match_reasons = {
                    "same_contact": True,
                    "same_amount": same_amount,
                    "amount": f"{a.amount:.2f}",
                    "other_amount": None if same_amount else f"{b.amount:.2f}",
                    "currency": currency,
                    "days_apart": days_apart,
                    "reference_match": ref_match,
                    "same_invoice_number": same_invoice_number,
                    "distinct_documents_possible": distinct_docs_possible,
                    "cross_contact": cross_contact,
                    "confidence": confidence,
                    "review": tier != "high",
                    "tier": tier,
                    "recurring": recurring,
                    "one_paid_one_outstanding": one_paid_one_out,
                    "risk": risk,
                }

                is_confirmed_dup = tier == "high"
                pct = int(round(confidence * 100))
                severity = "high" if tier == "high" else "medium"

                for subject, partner, is_orig in (
                    (original, duplicate, True),
                    (duplicate, original, False),
                ):
                    partner_label = (
                        (partner.reference or partner.invoice_number or "").strip()
                        or partner.transaction_id
                    )
                    partner_at = f"{partner.date.isoformat()}, {symbol}{partner.amount:.2f}"
                    if is_confirmed_dup and is_orig:
                        message = (
                            f"Likely has duplicate {partner_label} ({partner_at}; "
                            f"{basis}). Likely the original — keep this; void {partner_label}."
                        )
                    elif is_confirmed_dup:
                        message = (
                            f"Likely duplicate of {partner_label} ({partner_at}; "
                            f"{basis}). Recommended: void this one, keep {partner_label}."
                        )
                    elif recurring:
                        message = (
                            f"Possible recurring charge — also {partner_label} "
                            f"({partner_at}; {basis}). Looks like a subscription "
                            f"({pct}% match); please review — likely a legitimate "
                            f"recurring charge, not a duplicate."
                        )
                    else:
                        message = (
                            f"Possible match ({pct}%) with {partner_label} "
                            f"({partner_at}; {basis}). Please review — not a confirmed "
                            f"duplicate; keep both unless you confirm a true copy."
                        )
                    if distinct_docs_possible:
                        message += " Different invoice numbers — could be 2 distinct documents. Please check."
                    flagged.append(FlaggedIssue(
                        transaction_id=subject.transaction_id,
                        issue_type=issue_type,
                        severity=severity,
                        message=message[:280],
                        confidence=confidence,
                        duplicate_of_transaction_id=partner.transaction_id,
                        duplicate_of_invoice_number=(partner.invoice_number or "").strip() or None,
                        duplicate_of_date=partner.date,
                        this_is_likely_original=(is_orig if is_confirmed_dup else None),
                        match_reasons=match_reasons,
                    ))

    flagged.sort(key=lambda f: f.confidence or 0.0, reverse=True)
    return flagged


def _find_direction_mismatches(
    transactions: list[BatchTransaction],
    coa_type_lookup: dict[str, str],
) -> list[FlaggedIssue]:
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        code = (tx.current_account_code or "").strip()
        allowed = _allowed_account_types_for_doc(tx.type)
        current_type = coa_type_lookup.get(code, "")
        if code and allowed and current_type and current_type not in allowed:
            is_sale = (tx.type or "").strip().upper() in _SALES_DOC_TYPES
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="wrong_direction_account",
                severity="high",
                message=f"{'Sales invoice' if is_sale else 'Purchase bill'} coded to {current_type} account ({code})."[:140],
                current_code=code,
            ))
    return flagged


def _dominant(values: list[Any]) -> Optional[str]:
    cleaned = [str(value).strip() for value in values if value and str(value).strip()]
    return Counter(cleaned).most_common(1)[0][0] if cleaned else None


def _find_multi_account_suppliers(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    contact_alias: Optional[dict[str, str]] = None,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Xenon Multi-Account Suppliers: a contact whose postings span MORE THAN ONE
    account code (pure distinct-count — 2+ distinct → flag). Checked across every
    LINE ITEM of the contact's bills AND Money-Out bank payments — the account
    code lives on the line, not the header. The most-used account is treated as
    the 'usual' one; the differing postings are flagged with it as the suggestion.
    """
    alias = contact_alias or {}
    whitelist = frozenset(
        c.strip().upper() for c in (settings.multi_account_whitelist_contacts or ()) if c
    )
    # Every (transaction, account_code) pair from line items, grouped by contact.
    by_contact: dict[str, list[tuple[BatchTransaction, str]]] = defaultdict(list)
    for tx in transactions:
        for _line_no, code, _amount in _account_lines(tx):
            code = (code or "").strip()
            if code:
                by_contact[_contact_key(tx, alias)].append((tx, code))

    flagged: list[FlaggedIssue] = []
    for key, entries in by_contact.items():
        accounts = [code for _tx, code in entries]
        if len(set(accounts)) < 2:           # Xenon trigger: 2+ distinct accounts
            continue
        # Whitelisted suppliers are allowed to split across accounts — skip them.
        sample = entries[0][0]
        ids = {key.strip().upper(), (sample.contact_id or "").strip().upper(),
               sample.vendor_name.strip().upper()}
        if whitelist & ids:
            continue
        dominant = _dominant(accounts)        # the 'usual' account
        seen: set[str] = set()                # one flag per transaction
        for tx, code in entries:
            if code != dominant and tx.transaction_id not in seen:
                seen.add(tx.transaction_id)
                flagged.append(FlaggedIssue(
                    transaction_id=tx.transaction_id,
                    issue_type="multi_account_supplier",
                    severity="medium",
                    message=f"{tx.vendor_name} usually posts to {dominant}; this one is {code}."[:140],
                    suggested_code=dominant,
                    suggested_name=coa_lookup.get(dominant),
                    current_code=code,
                ))
    return flagged


def _find_multi_tax_code_suppliers(
    transactions: list[BatchTransaction],
    contact_alias: Optional[dict[str, str]] = None,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Xenon Multi-Tax-Code Suppliers: a contact whose postings use MORE THAN ONE
    tax code (pure distinct-count — 2+ distinct → flag). Checked across every LINE
    ITEM of the contact's bills AND Money-Out bank payments — tax lives on the
    line, not the header. The most-used tax code is the 'usual' one; the differing
    lines are flagged with it as the suggestion.
    """
    alias = contact_alias or {}
    # Every (transaction, tax_code) pair from line items, grouped by contact.
    by_contact: dict[str, list[tuple[BatchTransaction, str]]] = defaultdict(list)
    for tx in transactions:
        for _line_no, tax in _tax_lines(tx):
            tax = (tax or "").strip().upper()
            if tax:
                by_contact[_contact_key(tx, alias)].append((tx, tax))

    flagged: list[FlaggedIssue] = []
    for entries in by_contact.values():
        codes = [tax for _tx, tax in entries]
        if len(set(codes)) < 2:              # Xenon trigger: 2+ distinct tax codes
            continue
        dominant = _dominant(codes)          # the 'usual' tax code
        seen: set[str] = set()               # one flag per transaction
        for tx, tax in entries:
            if tax != dominant and tx.transaction_id not in seen:
                seen.add(tx.transaction_id)
                flagged.append(FlaggedIssue(
                    transaction_id=tx.transaction_id,
                    issue_type="multi_tax_code_supplier",
                    severity="medium",
                    message=f"{tx.vendor_name} usually uses tax {dominant}; this one is {tax}."[:140],
                    suggested_code=dominant,
                    current_code=tax,
                ))
    return flagged


def _find_unexpected_accounts(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    contact_defaults: Optional[dict[str, dict[str, Optional[str]]]] = None,
) -> list[FlaggedIssue]:
    """Flag a transaction whose account code differs from the contact's DEFAULT
    (sales default for customer invoices / money IN, purchase default for
    supplier bills / money OUT).

    Covers ACCREC/ACCPAY invoices+bills AND bank transactions (RECEIVE → sales,
    SPEND → purchase) — exactly the four Xenon transaction types.

    Xenon rule: a contact with NO default configured is SILENT — without a
    baseline there is nothing to call "unexpected". Frequency-based detection
    (compare a posting against the contact's OWN history) is the separate
    Multi-Account Suppliers check, deliberately not duplicated here.
    """
    if not contact_defaults:
        return []
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        defaults = contact_defaults.get((tx.contact_id or "").strip())
        if not defaults:
            continue
        doc_type = (tx.type or "").strip().upper()
        is_sales = doc_type in _SALES_DOC_TYPES or doc_type in _MONEY_IN_TYPES
        default = defaults.get("sales") if is_sales else defaults.get("purchase")
        used = (tx.current_account_code or "").strip()
        if default and used and used != default:
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="unexpected_account",
                severity="medium",
                message=f"{tx.vendor_name} usually posts to {default}; this used {used}."[:140],
                current_code=used,
                suggested_code=default,
                suggested_name=coa_lookup.get(default),
            ))
    return flagged


def _find_unexpected_tax_codes(
    transactions: list[BatchTransaction],
    contact_defaults: Optional[dict[str, dict[str, Optional[str]]]] = None,
) -> list[FlaggedIssue]:
    """Flag a transaction whose tax code differs from the contact's DEFAULT tax
    (sales tax for customer invoices / money IN, purchase tax for supplier bills
    / money OUT). Covers ACCREC/ACCPAY AND bank transactions (RECEIVE/SPEND).
    Same Xenon rule: a contact with no default tax configured is silent.
    """
    if not contact_defaults:
        return []
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        defaults = contact_defaults.get((tx.contact_id or "").strip())
        if not defaults:
            continue
        doc_type = (tx.type or "").strip().upper()
        is_sales = doc_type in _SALES_DOC_TYPES or doc_type in _MONEY_IN_TYPES
        default = defaults.get("sales_tax") if is_sales else defaults.get("purchase_tax")
        used = (tx.tax_code or "").strip().upper()
        if default and used and used != default.strip().upper():
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="unexpected_tax_code",
                severity="medium",
                message=f"{tx.vendor_name} usually uses tax {default}; this used {used}."[:140],
                current_code=used,
                suggested_code=default,
            ))
    return flagged


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


_FIXED_ASSET_TYPES = frozenset({"FIXED", "FIXEDASSET"})


def _find_low_cost_fixed_assets(
    transactions: list[BatchTransaction],
    coa_type_lookup: dict[str, str],
    coa_lookup: dict[str, str],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Flag a transaction line posted to a FIXED-ASSET account for an amount BELOW
    the capitalisation threshold (``low_cost_asset_max``, default £10k). Such
    items should usually be expensed, not capitalised.

    Pure deterministic — account TYPE + line AMOUNT only. No contact, no date, no
    LLM (so it always runs, even when the LLM is unavailable).
    """
    threshold = settings.low_cost_asset_max
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        currency = (tx.currency_code or "GBP").strip().upper()
        symbol = "£" if currency == "GBP" else f"{currency} "
        for line_no, code, amount in _account_lines(tx):
            code = (code or "").strip()
            if not code or amount is None:
                continue
            if (coa_type_lookup.get(code) or "").strip().upper() not in _FIXED_ASSET_TYPES:
                continue
            amt = abs(amount)
            if amt <= 0 or amt >= threshold:
                continue
            name = coa_lookup.get(code) or code
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="low_cost_fixed_asset",
                severity="medium",
                message=(
                    f"{tx.vendor_name}: {symbol}{amt:.2f} posted to fixed-asset "
                    f"account {code} ({name}) — below the {symbol}{threshold:.0f} "
                    f"capitalisation threshold; consider expensing instead."
                )[:200],
                current_code=code,
                # No suggested_code on purpose → the UI shows a "?" because there
                # is no single correct target account; ``reasoning`` explains why
                # and ``recode_to_account_type`` tells the UI which accounts to
                # offer in the manual-fix dropdown.
                reasoning=(
                    f"This line sits in {code} ({name}) — a fixed-asset account — "
                    f"but {symbol}{amt:.2f} is below your {symbol}{threshold:.0f} "
                    f"capitalisation threshold, so it is usually too small to "
                    f"capitalise. Recommended: re-code it to an EXPENSE account. "
                    f"There is no single correct expense account, so this is a "
                    f"suggestion to review (hence the '?'), not a one-click fix — "
                    f"pick the expense account that fits."
                ),
                match_reasons={
                    "line_no": line_no,
                    "account_code": code,
                    "account_name": name,
                    "current_account_type": "FIXED",
                    "line_amount": f"{amt:.2f}",
                    "threshold": f"{threshold:.2f}",
                    "currency": currency,
                    # The fix is directional: a too-cheap fixed asset should be
                    # EXPENSED. Frontend: populate the re-code dropdown with the
                    # EXPENSE accounts from /coding-options/.
                    "recommended_action": "expense",
                    "recode_to_account_type": "EXPENSE",
                },
            ))
    return flagged


_CAPITAL_REVIEW_KEYWORDS = ("repair", "maintenance", "printing", "stationery")


def _find_capital_items(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    coa_type_lookup: dict[str, str],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Mirror of low_cost_fixed_asset: a line posted to a MONITORED EXPENSE
    account for an amount ABOVE the threshold (``capital_item_threshold``) — it
    may really be a capital item (fixed asset) mis-coded to an expense (e.g. a
    £90k laptop booked to Repairs & Maintenance instead of Computer Equipment).

    Monitored = the codes in ``capital_monitored_accounts`` when set, else any
    EXPENSE-type account whose name looks capital-suspicious (repairs / printing
    / maintenance / stationery). Pure deterministic — account + amount, no LLM.
    """
    threshold = settings.capital_item_threshold
    monitored = {c.strip().upper() for c in settings.capital_monitored_accounts if c.strip()}
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        currency = (tx.currency_code or "GBP").strip().upper()
        symbol = "£" if currency == "GBP" else f"{currency} "
        for line_no, code, amount in _account_lines(tx):
            code = (code or "").strip()
            if not code or amount is None:
                continue
            name = coa_lookup.get(code) or code
            if monitored:
                if code.upper() not in monitored:
                    continue
            else:
                # name-keyword fallback, restricted to P&L EXPENSE accounts so we
                # never flag a balance-sheet line.
                if (coa_type_lookup.get(code) or "").strip().upper() not in _EXPENSE_ACCOUNT_TYPES:
                    continue
                if not any(k in name.lower() for k in _CAPITAL_REVIEW_KEYWORDS):
                    continue
            amt = abs(amount)
            if amt <= threshold:
                continue
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type="capital_item_review",
                severity="medium",
                message=(
                    f"{tx.vendor_name}: {symbol}{amt:.2f} posted to expense account "
                    f"{code} ({name}) — above the {symbol}{threshold:.0f} threshold; "
                    f"may be a capital item (fixed asset), not an expense."
                )[:200],
                current_code=code,
                # Mirror of low_cost_fixed_asset: no single correct target →
                # "?" in the UI, ``reasoning`` explains it, ``recode_to_account_type``
                # tells the UI which accounts to offer in the fix dropdown.
                reasoning=(
                    f"This line sits in {code} ({name}) — an expense account — "
                    f"but {symbol}{amt:.2f} is above your {symbol}{threshold:.0f} "
                    f"threshold, so it may really be a capital item that should be "
                    f"a FIXED asset (capitalised + depreciated), not expensed in "
                    f"one go. Recommended: review and, if it is an asset, re-code "
                    f"it to a fixed-asset account. There is no single correct "
                    f"target, so this is a suggestion to review (hence the '?')."
                ),
                match_reasons={
                    "line_no": line_no,
                    "account_code": code,
                    "account_name": name,
                    "current_account_type": "EXPENSE",
                    "line_amount": f"{amt:.2f}",
                    "threshold": f"{threshold:.2f}",
                    "currency": currency,
                    # Directional fix: a too-big expense may be a CAPITAL item.
                    # Frontend: offer FIXED-asset accounts from /coding-options/.
                    "recommended_action": "capitalise",
                    "recode_to_account_type": "FIXED",
                },
            ))
    return flagged


def _is_vague_account(code: Optional[str], coa_lookup: dict[str, str], extra_codes: frozenset[str]) -> bool:
    clean = (code or "").strip()
    return bool(clean and (clean.upper() in extra_codes or any(keyword in (coa_lookup.get(clean) or "").lower() for keyword in _VAGUE_ACCOUNT_NAME_KEYWORDS)))


def _find_misallocated_items(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    extra = frozenset(settings.misallocated_vague_codes or ())
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        for line_no, code, amount in _account_lines(tx):
            if amount is None or abs(amount) < settings.misallocated_materiality:
                continue
            if _is_vague_account(code, coa_lookup, extra):
                where = f" (line {line_no})" if line_no else ""
                flagged.append(FlaggedIssue(
                    transaction_id=tx.transaction_id,
                    issue_type="misallocated_item",
                    severity="medium",
                    message=f"{tx.vendor_name} £{abs(amount):.2f} coded to vague account{where} - review."[:140],
                    current_code=(code or "").strip() or None,
                ))
                break
    return flagged


def _tax_missing_ignored(
    tx: BatchTransaction,
    ignore_accounts: frozenset[str],
    ignore_contacts: frozenset[str],
) -> bool:
    account = (tx.current_account_code or "").strip().upper()
    contact = (tx.contact_id or "").strip().upper()
    name = tx.vendor_name.strip().upper()
    return bool((account and account in ignore_accounts) or (contact and contact in ignore_contacts) or (name and name in ignore_contacts))


# "No VAT" / "Outside scope" — the tax codes the tax-missing checks treat as
# MISSING. Deliberately EXCLUDES zero-rated / exempt: those are intentional 0%
# treatments with a real code, not missing tax.
_NO_VAT_TAX_CODES = {"NONE", "NOTAX", "NOVAT"}

# Purchase-side expense accounts that LEGITIMATELY carry no VAT — ignored so we
# don't false-flag them (matched on account NAME, plus the configurable code
# ignore-list). Wages, tax payments, depreciation, donations, etc.
_PURCHASE_NO_VAT_BY_NATURE = (
    "wage", "salary", "salaries", "payroll", "employer ni", "national insurance",
    "paye", "director remuneration", "pension", "depreciat", "amortis",
    "corporation tax", "income tax", "donation", "rates", "grant", "dividend",
)


def _is_no_vat_code(tax: Optional[str]) -> bool:
    """True for 'No VAT' / 'Outside scope' codes — NOT zero-rated / exempt."""
    norm = (tax or "").strip().upper().replace(" ", "")
    if not norm:
        return False
    return norm in _NO_VAT_TAX_CODES or "OUTSIDE" in norm


def _find_tax_missing(
    transactions: list[BatchTransaction],
    account_types: set[str],
    issue_type: str,
    noun: str,
    coa_lookup: dict[str, str],
    coa_type_lookup: dict[str, str],
    settings: AuditSettings,
    *,
    ignore_name_keywords: tuple[str, ...] = (),
) -> list[FlaggedIssue]:
    """Xenon tax-missing: a line on an in-scope account (Sales/Income for sales,
    Expense/Asset for purchase) with a No-VAT / Outside-Scope tax code → flag for
    review. Per LINE ITEM, across bills/invoices AND Money In / Money Out (the
    account-type filter routes each line to the right check). Genuine no-VAT cases
    are suppressed via the account name-keyword ignore (purchase) + the
    configurable account/contact ignore-lists."""
    ignore_codes = frozenset(
        c.strip().upper() for c in (settings.tax_missing_ignore_accounts or ()) if c
    )
    ignore_contacts = frozenset(
        c.strip().upper() for c in (settings.tax_missing_ignore_contacts or ()) if c
    )
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        contact = (tx.contact_id or "").strip().upper()
        name = (tx.vendor_name or "").strip().upper()
        if (contact and contact in ignore_contacts) or (name and name in ignore_contacts):
            continue
        for _line_no, code, _amount, tax in _lines_with_account_and_tax(tx):
            acct = (code or "").strip()
            if not acct or acct.upper() in ignore_codes:
                continue
            if (coa_type_lookup.get(acct) or "").strip().upper() not in account_types:
                continue
            acct_name = (coa_lookup.get(acct) or "").lower()
            if any(kw in acct_name for kw in ignore_name_keywords):
                continue   # no-VAT-by-nature (wages, depreciation, tax, …)
            if not _is_no_vat_code(tax):
                continue
            flagged.append(FlaggedIssue(
                transaction_id=tx.transaction_id,
                issue_type=issue_type,
                severity="medium",
                message=(f"{tx.vendor_name}: {noun} on {acct} "
                         f"({coa_lookup.get(acct) or acct}) has no VAT "
                         f"(tax {(tax or '').strip() or 'none'}) - review.")[:140],
                current_code=(tax or "").strip() or None,
            ))
            break   # one flag per document
    return flagged


def _find_purchase_tax_missing(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    coa_type_lookup: dict[str, str],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    return _find_tax_missing(
        transactions, _EXPENSE_ACCOUNT_TYPES, "purchase_tax_missing", "bill",
        coa_lookup, coa_type_lookup, settings,
        ignore_name_keywords=_PURCHASE_NO_VAT_BY_NATURE,
    )


def _find_sales_tax_missing(
    transactions: list[BatchTransaction],
    coa_lookup: dict[str, str],
    coa_type_lookup: dict[str, str],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    return _find_tax_missing(
        transactions, _REVENUE_ACCOUNT_TYPES, "sales_tax_missing", "income",
        coa_lookup, coa_type_lookup, settings,
    )


_OUTPUT_TAX_KEYWORDS = {"OUTPUT", "SALES", "ZERORATEDSUPPLIES", "EXEMPTOUTPUT", "GSTONIMPORTS"}
_INPUT_TAX_KEYWORDS = {"INPUT", "PURCHASE", "BASEXCLUSIVE", "EXEMPTINPUT"}


def _is_wrong_for_bill(code: str, tax_dir: Optional[dict[str, tuple]]) -> bool:
    clean = code.strip().upper()
    if tax_dir and clean in tax_dir and tax_dir[clean][0] is not None:
        return tax_dir[clean][0] is False
    return any(keyword in clean for keyword in _OUTPUT_TAX_KEYWORDS)


def _is_wrong_for_invoice(code: str, tax_dir: Optional[dict[str, tuple]]) -> bool:
    clean = code.strip().upper()
    if tax_dir and clean in tax_dir and tax_dir[clean][1] is not None:
        return tax_dir[clean][1] is False
    return any(keyword in clean for keyword in _INPUT_TAX_KEYWORDS)


def _tax_lines_with_amounts(
    tx: BatchTransaction,
) -> list[tuple[Optional[int], Optional[str], Optional[Decimal], Optional[Decimal]]]:
    """(line_no, tax_code, net_amount, tax_amount) per line — for the wrong-tax
    checks, which surface the Net + Tax columns Xenon shows."""
    if tx.line_items:
        return [
            (i + 1, li.tax_code, li.amount, li.tax_amount)
            for i, li in enumerate(tx.line_items)
        ]
    return [(None, tx.tax_code, tx.amount, None)]


def _tax_direction_reasons(code: str, net: Optional[Decimal], tax_amt: Optional[Decimal]) -> dict:
    reasons: dict = {"tax_code": code}
    if net is not None:
        reasons["net_amount"] = f"{abs(net):.2f}"
    if tax_amt is not None:
        reasons["tax_amount"] = f"{abs(tax_amt):.2f}"
    return reasons


def _find_sales_tax_on_bills(
    transactions: list[BatchTransaction],
    tax_dir: Optional[dict[str, tuple]] = None,
) -> list[FlaggedIssue]:
    """A purchase document (bill OR Money Out) using a SALES-side VAT code. Money
    Out (SPEND) is included so the 'Show Bank payments too' toggle can reveal it;
    the frontend hides bank items by default."""
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        if (tx.type or "").strip().upper() not in (_PURCHASE_DOC_TYPES | _MONEY_OUT_TYPES):
            continue
        for line_no, code, net, tax_amt in _tax_lines_with_amounts(tx):
            clean = (code or "").strip().upper()
            if clean and _is_wrong_for_bill(clean, tax_dir):
                where = f" (line {line_no})" if line_no else ""
                flagged.append(FlaggedIssue(
                    transaction_id=tx.transaction_id,
                    issue_type="sales_tax_on_bills",
                    severity="high",
                    message=f"{tx.vendor_name} bill uses sales tax code {clean}{where}."[:140],
                    current_code=clean,
                    match_reasons=_tax_direction_reasons(clean, net, tax_amt),
                ))
                break
    return flagged


def _find_purchase_tax_on_invoices(
    transactions: list[BatchTransaction],
    tax_dir: Optional[dict[str, tuple]] = None,
) -> list[FlaggedIssue]:
    """A sales document (invoice OR Money In) using a PURCHASE-side VAT code.
    Money In (RECEIVE) included for the 'Show Bank payments too' toggle."""
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        if (tx.type or "").strip().upper() not in (_SALES_DOC_TYPES | _MONEY_IN_TYPES):
            continue
        for line_no, code, net, tax_amt in _tax_lines_with_amounts(tx):
            clean = (code or "").strip().upper()
            if clean and _is_wrong_for_invoice(clean, tax_dir):
                where = f" (line {line_no})" if line_no else ""
                flagged.append(FlaggedIssue(
                    transaction_id=tx.transaction_id,
                    issue_type="purchase_tax_on_invoices",
                    severity="high",
                    message=f"{tx.vendor_name} invoice uses purchase tax code {clean}{where}."[:140],
                    current_code=clean,
                    match_reasons=_tax_direction_reasons(clean, net, tax_amt),
                ))
                break
    return flagged


def _find_undocumented_bills(
    transactions: list[BatchTransaction],
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[FlaggedIssue]:
    """Xenon Undocumented Bills: a supplier BILL (or, via the 'Show direct
    payments' toggle, a Money Out) with NO attachment in Xero (HasAttachments
    False). Filters: minimum amount, tax-only, and ignored contacts. Money Out
    is always flagged here; the frontend hides it by default (exclude_bank_items)."""
    ignore_contacts = frozenset(
        c.strip().upper() for c in (settings.undocumented_ignore_contacts or ()) if c
    )
    flagged: list[FlaggedIssue] = []
    for tx in transactions:
        doc_type = (tx.type or "").strip().upper()
        is_bill = doc_type == "ACCPAY"
        if not (is_bill or doc_type in _MONEY_OUT_TYPES):
            continue
        # Only flag when we KNOW there is no attachment. None = not fetched → skip
        # (never flag on missing data).
        if tx.has_attachments is not False:
            continue
        contact = (tx.contact_id or "").strip().upper()
        name = (tx.vendor_name or "").strip().upper()
        if (contact and contact in ignore_contacts) or (name and name in ignore_contacts):
            continue
        if abs(tx.amount) < settings.undocumented_min_amount:
            continue
        if settings.undocumented_tax_only and not (tx.tax_total and abs(tx.tax_total) > 0):
            continue
        reasons: dict = {
            "net_amount": f"{abs(tx.amount):.2f}",
            "currency": (tx.currency_code or "GBP").strip().upper(),
        }
        if tx.tax_total is not None:
            reasons["tax_amount"] = f"{abs(tx.tax_total):.2f}"
        flagged.append(FlaggedIssue(
            transaction_id=tx.transaction_id,
            issue_type="undocumented_bill",
            severity="medium",
            message=(f"{tx.vendor_name}: {'bill' if is_bill else 'payment'} "
                     f"£{abs(tx.amount):.2f} has no attachment in Xero.")[:140],
            match_reasons=reasons,
        ))
    return flagged


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


def find_amount_outlier_candidates(
    transactions: list[BatchTransaction],
    contact_alias: Optional[dict[str, str]] = None,
    settings: AuditSettings = DEFAULT_SETTINGS,
) -> list[dict]:
    alias = contact_alias or {}
    by_contact: dict[str, list[BatchTransaction]] = defaultdict(list)
    for tx in transactions:
        if tx.amount > 0:
            by_contact[_contact_key(tx, alias)].append(tx)
    candidates: list[dict] = []
    for txns in by_contact.values():
        if len(txns) < settings.outlier_min_txns:
            continue
        usual = Decimal(str(median([tx.amount for tx in txns])))
        if usual <= 0:
            continue
        for tx in txns:
            if tx.amount >= usual * settings.outlier_multiple and tx.amount >= settings.outlier_min_amount:
                candidates.append({
                    "tx": tx,
                    "median": usual,
                    "ratio": float(tx.amount / usual),
                    "vendor_txn_count": len(txns),
                    "usual_account": _dominant([item.current_account_code for item in txns]),
                    "usual_tax": _dominant([item.tax_code for item in txns]),
                })
    return candidates


def amount_outlier_flag(candidate: dict) -> FlaggedIssue:
    tx = candidate["tx"]
    currency = (tx.currency_code or "GBP").strip().upper()
    symbol = "£" if currency == "GBP" else f"{currency} "
    return FlaggedIssue(
        transaction_id=tx.transaction_id,
        issue_type="amount_outlier",
        severity="medium",
        message=(
            f"{tx.vendor_name} usually ~{symbol}{candidate['median']:.2f}, "
            f"but this is {symbol}{tx.amount:.2f} ({candidate['ratio']:.1f}x higher) - verify."
        )[:140],
        confidence=0.85,
    )


def _find_amount_outliers(
    transactions: list[BatchTransaction],
    contact_alias: Optional[dict[str, str]] = None,
) -> list[FlaggedIssue]:
    return [
        amount_outlier_flag(candidate)
        for candidate in find_amount_outlier_candidates(transactions, contact_alias)
    ]
