"""Canonical registry of every audit check.

The frontend's Audit Configuration screen toggles these on/off per
company. Each rule key matches the ``issue_type`` the engine emits, so
disabling a key simply drops that issue_type from the results (and skips
the relevant LLM pass when possible).

``built = False`` keys are surfaced for completeness (they appear in the
UI) but the engine doesn't emit them yet — toggling them is a no-op.
"""
from __future__ import annotations

from typing import Any

# group → list of (key, label, built)
_GROUPS: dict[str, list[tuple[str, str, bool]]] = {
    "Bank & Reconciliation": [
        ("unprocessed_bank", "Unprocessed bank", False),
        ("unreconciled_bank", "Unreconciled bank (Received/Spent)", True),
        ("bank_balance_check", "Bank balance check", True),
        ("opening_balance_difference", "Opening balance differences", True),
        ("bill_direct_payment", "Bill paid directly (vs unpaid bill)", True),
        ("invoice_direct_deposit", "Invoice paid directly (vs unpaid invoice)", True),
    ],
    "Duplicates": [
        ("duplicate_invoice", "Duplicate invoices", True),
        ("duplicate_bill", "Duplicate bills", True),
        ("duplicate_credit_note", "Duplicate credit notes", True),
        ("duplicate_contact", "Duplicate contacts", True),
    ],
    "Tax & VAT": [
        ("missing_tax", "Missing tax code (any document)", True),
        ("invalid_tax_code", "Invalid tax code", True),
        ("sales_tax_missing", "Sales tax missing", True),
        ("purchase_tax_missing", "Purchase tax missing", True),
        ("sales_tax_on_bills", "Sales tax on bills (suspicious)", True),
        ("purchase_tax_on_invoices", "Purchase tax on invoices (suspicious)", True),
        ("unexpected_tax_code", "Unexpected tax code", True),
        ("multi_tax_code_supplier", "Multi-tax suppliers (vendor drift)", True),
    ],
    "Categorisation & Coding": [
        ("wrong_category", "Wrong category", True),
        ("unexpected_account", "Unexpected account", True),
        ("multi_account_supplier", "Multi-account suppliers (vendor drift)", True),
        ("misallocated_item", "Misallocated items (vague account)", True),
        ("wrong_direction_account", "Wrong-direction account (income vs expense)", True),
        ("invoice_or_direct_booking", "Invoice or direct booking", True),
        ("bill_or_direct_booking", "Bill or direct booking", True),
        ("anomaly", "Anomaly (LLM)", True),
        ("amount_outlier", "Amount outlier vs vendor history", True),
    ],
    "Date & Ageing": [
        ("future_dated", "Future-dated documents", True),
        ("old_unpaid_bill", "Overdue bills (we owe)", True),
        ("old_unpaid_invoice", "Overdue invoices (we're owed)", True),
        ("old_unsettled_sales_credit", "Old sales credit notes", True),
        ("old_unsettled_purchase_credit", "Old purchase credit notes", True),
    ],
    "Approval & Status": [
        ("unapproved_invoice", "Unapproved invoices", True),
        ("unapproved_bill", "Unapproved bills", True),
    ],
    "Contacts": [
        ("missing_vendor", "Missing vendor", True),
        ("contact_defaults", "Contact defaults missing", True),
        ("inactive_contact", "Inactive contacts", True),
    ],
    "Document Integrity": [
        ("missing_invoice_number", "Missing invoice number", True),
        ("undocumented_bill", "Undocumented bills (no attachment)", True),
    ],
    "Fixed Assets": [
        ("low_cost_fixed_asset", "Low-cost fixed asset", True),
        ("capital_item_review", "Capital item review", True),
    ],
    "Currency": [
        ("currency_mismatch", "Currency mismatch", False),
    ],
}

# Flat set of all valid rule keys.
ALL_RULE_KEYS: set[str] = {
    key for rules in _GROUPS.values() for (key, _, _) in rules
}


def rule_catalog(disabled_rules: set[str]) -> list[dict[str, Any]]:
    """Return the full grouped rule list with each rule's enabled state,
    ready for the frontend to render the Audit Configuration screen."""
    out: list[dict[str, Any]] = []
    for group, rules in _GROUPS.items():
        out.append({
            "group": group,
            "rules": [
                {
                    "key": key,
                    "label": label,
                    "built": built,
                    "enabled": key not in disabled_rules,
                }
                for (key, label, built) in rules
            ],
        })
    return out


def total_checks() -> int:
    return len(ALL_RULE_KEYS)
