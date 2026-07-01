"""Wraps the ``/api/v1/suggest-fix`` rules-engine call for the trapped
row context. Always returns a populated ``SuggestFixSuggestion`` — when
the rules engine is down / gated off, ``available`` is False and the
suggestion defaults stand so the frontend can render a manual-fix
fallback without null-checks everywhere.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck import ai_client
from app.modules.healthcheck.models import Company
from app.modules.healthcheck.repository import HealthCheckResultRepository
from app.modules.healthcheck.schemas import (
    SuggestFixResponse,
    SuggestFixSuggestion,
)
from app.modules.healthcheck.xero_links import xero_deep_link

logger = logging.getLogger("eazycapture.suggest_fix")


class SuggestFixService:
    """Builds the per-row suggest-fix response. Pure orchestrator —
    actual LLM call lives in :mod:`app.modules.healthcheck.ai_client`."""

    def __init__(self, db: AsyncSession, redis=None) -> None:
        self._db = db
        self._redis = redis
        self._repo = HealthCheckResultRepository(db)

    async def get_suggestion(
        self,
        row_id: UUID,
        company_id: UUID,
    ) -> SuggestFixResponse:
        row = await self._repo.find_by_id(row_id, company_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Trapped row not found for this company.",
            )

        rule_id = _primary_rule_id(row.result, row.document_type)
        transaction_payload = _build_transaction_payload(row)
        company = await self._db.get(Company, company_id)
        shortcode = (
            (company.xero_shortcode or "").strip() or None
            if company is not None else None
        )
        xero_url = xero_deep_link(row.document_type, row.document_id, shortcode)

        # Fast path: fully deterministic rules don't need LLM at all.
        deterministic = _deterministic_fix(rule_id, transaction_payload)
        if deterministic is not None:
            return SuggestFixResponse(
                row_id=row.id,
                document_id=row.document_id,
                document_type=row.document_type,
                xero_url=xero_url,
                available=True,
                suggestion=deterministic,
            )

        # For recoding rules, inject the cached COA so the LLM picks
        # from the real account codes in this org — not generic guesses.
        if rule_id in _RECODE_ISSUE_TYPES and self._redis is not None:
            import json as _json
            try:
                raw_coa = await self._redis.get(f"xero_coa:{company_id}")
                if raw_coa:
                    coa = _json.loads(raw_coa)
                    coa_compact = [
                        {"code": a["code"], "name": a["name"], "type": a.get("type", "")}
                        for a in coa
                        if a.get("code") and a.get("name")
                    ]
                    transaction_payload["chart_of_accounts"] = coa_compact
            except Exception:
                pass

        # Slow path: LLM needed for context-dependent rules.
        raw = await ai_client.suggest_fix(
            rule_id=rule_id, transaction=transaction_payload,
        )
        if raw is None:
            return SuggestFixResponse(
                row_id=row.id,
                document_id=row.document_id,
                document_type=row.document_type,
                xero_url=xero_url,
                available=False,
                reason="Rules engine unavailable or feature gate disabled.",
                suggestion=SuggestFixSuggestion(),
            )

        suggestion = _normalise_suggestion(raw)

        # Fallback: if the LLM dropped line_item_updates for a recoding issue,
        # reconstruct it from rules-engine suggestion or vendor-name inference.
        if suggestion.line_item_updates is None:
            rule = (transaction_payload.get("issue_type") or rule_id or "")
            if rule in _RECODE_ISSUE_TYPES:
                target_code = (
                    (transaction_payload.get("suggested_account_code") or "").strip()
                    or _infer_account_from_vendor(
                        transaction_payload.get("vendor_name") or ""
                    )
                    or _infer_account_from_steps(suggestion.human_steps)
                )
                if target_code:
                    suggestion = suggestion.model_copy(
                        update={"line_item_updates": {"AccountCode": target_code}}
                    )

        return SuggestFixResponse(
            row_id=row.id,
            document_id=row.document_id,
            document_type=row.document_type,
            xero_url=xero_url,
            available=True,
            suggestion=suggestion,
        )


# ------------------------- helpers ---------------------------------

_RECODE_ISSUE_TYPES = {
    "wrong_category", "wrong_direction_account",
    "unexpected_account", "multi_account_supplier",
}

_VENDOR_ACCOUNT_MAP = [
    # Office furniture / fittings
    ("furniture", "461"), ("fittings", "461"), ("interiors", "461"),
    # Telecoms / broadband
    ("bt ", "429"), ("virgin media", "429"), ("sky ", "429"), ("vodafone", "429"),
    ("o2", "429"), ("ee ", "429"), ("three ", "429"), ("talktalk", "429"),
    # Software / cloud subscriptions
    ("aws", "485"), ("azure", "485"), ("google cloud", "485"), ("xero", "485"),
    ("salesforce", "485"), ("microsoft", "485"), ("adobe", "485"),
    ("slack", "485"), ("dropbox", "485"), ("notion", "485"), ("github", "485"),
    # IT hardware / computer supplies
    ("pc ", "461"), ("dell", "461"), ("hp ", "461"), ("lenovo", "461"),
    ("apple ", "461"), ("complete", "461"), ("computers", "461"), ("laptop", "461"),
    ("server", "461"), ("monitor", "461"), ("keyboard", "461"), ("printer", "461"),
    # Travel
    ("uber", "493"), ("addison lee", "493"), ("taxi", "493"), ("trainline", "493"),
    ("national rail", "493"), ("eurostar", "493"), ("heathrow", "493"),
    # Subsistence
    ("tesco", "425"), ("sainsbury", "425"), ("waitrose", "425"), ("costa", "425"),
    ("starbucks", "425"), ("pret", "425"), ("greggs", "425"),
    # Motor / fuel
    ("shell", "437"), ("bp ", "437"), ("fuel", "437"), ("esso", "437"),
    ("petrol", "437"), ("diesel", "437"),
    # Office supplies
    ("amazon", "461"), ("staples", "461"), ("ryman", "461"),
]

# Map a wrongly-used fixed asset account to its closest expense equivalent.
_FIXED_TO_EXPENSE_MAP = {
    "710": "461",  # Office Equipment FIXED → Office Equipment expense
    "720": "461",  # Computer Equipment FIXED → IT/Computer Equipment expense
    "740": "461",  # Furniture FIXED → Furniture & Fittings expense
    "750": "437",  # Motor Vehicles FIXED → Motor Expenses
    "760": "461",  # Plant & Machinery FIXED → Equipment expense
}


def _infer_account_from_vendor(vendor: str) -> Optional[str]:
    v = vendor.lower()
    for keyword, code in _VENDOR_ACCOUNT_MAP:
        if keyword in v:
            return code
    return None


def _deterministic_fix(
    rule_id: str,
    tx: dict[str, Any],
) -> Optional[SuggestFixSuggestion]:
    """Return a fully-computed fix for rules where no LLM reasoning is needed.
    Returns None for rules that genuinely require LLM context."""
    flagged = (tx.get("flagged_items") or [])
    first = flagged[0] if flagged else {}
    suggested_code = str(first.get("suggested_code") or "").strip()
    dup_of_id = str(first.get("duplicate_of_transaction_id") or "").strip()
    dup_of_inv = str(first.get("duplicate_of_invoice_number") or "").strip()
    is_original = first.get("this_is_likely_original")
    doc_id = str(tx.get("transaction_id") or "")

    if rule_id in ("duplicate_invoice", "duplicate_bill", "duplicate_credit_note"):
        # Credit notes void via a DIFFERENT Xero endpoint (/CreditNotes/, not
        # /Invoices/); the document noun changes the human steps too.
        is_credit = rule_id == "duplicate_credit_note"
        endpoint = "CreditNotes" if is_credit else "Invoices"
        noun = "credit note" if is_credit else "invoice"
        if is_original is None:
            # Review-tier pair: not a confirmed duplicate, so suggest a
            # side-by-side review rather than a void.
            other = dup_of_inv or f"the matching {noun}"
            return SuggestFixSuggestion(
                fix_strategy="review_possible_duplicate",
                xero_action="",
                human_steps=[
                    f"Open this {noun} and {other} side by side in Xero.",
                    "Confirm whether they're the same charge, or a normal "
                    "recurring/second document.",
                    "Void one ONLY if you confirm a true duplicate; otherwise "
                    "dismiss the match.",
                ],
                rationale=(
                    "Possible match, not a confirmed duplicate — neither side is "
                    "marked original. Review both before voiding anything."
                ),
                estimated_minutes=3,
            )
        if is_original:
            # Keep this one — void the sibling
            target = dup_of_id or doc_id
            return SuggestFixSuggestion(
                fix_strategy="void_duplicate",
                xero_action=f"PUT /{endpoint}/{target} {{\"Status\":\"VOIDED\"}}",
                field_updates={"Status": "VOIDED"} if target == doc_id else None,
                target_transaction_id=target or None,
                human_steps=[
                    f"Open {dup_of_inv or 'the duplicate'} in Xero.",
                    "Void it: Edit → Status → Voided.",
                    f"Confirm this original {noun} stays.",
                ],
                rationale=f"This is the original. Void the duplicate ({dup_of_inv or dup_of_id}) to remove the double-count.",
                estimated_minutes=2,
            )
        else:
            # This is the duplicate — void it
            return SuggestFixSuggestion(
                fix_strategy="void_duplicate",
                xero_action=f"PUT /{endpoint}/{doc_id} {{\"Status\":\"VOIDED\"}}",
                field_updates={"Status": "VOIDED"},
                target_transaction_id=doc_id or None,
                human_steps=[
                    f"Open this {noun} in Xero.",
                    "Void it: Edit → Status → Voided.",
                    f"Keep {dup_of_inv or 'the original'} — do not void that one.",
                ],
                rationale=f"This is the duplicate. Void it and keep {dup_of_inv or 'the original'}.",
                estimated_minutes=2,
            )

    if rule_id in ("unapproved_invoice", "unapproved_bill"):
        noun = "invoice" if rule_id == "unapproved_invoice" else "bill"
        return SuggestFixSuggestion(
            fix_strategy="approve_document",
            xero_action=f"PUT /Invoices/{doc_id} {{\"Status\":\"AUTHORISED\"}}",
            field_updates={"Status": "AUTHORISED"},
            human_steps=[
                f"Open this {noun} in Xero.",
                "Click Approve (or Submit → Approve).",
                "Confirm amount and account code before approving.",
            ],
            rationale=f"The {noun} is still in Draft. Approving it books the transaction into the ledger.",
            estimated_minutes=2,
        )

    if rule_id == "purchase_tax_missing":
        return SuggestFixSuggestion(
            fix_strategy="add_purchase_tax",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType=INPUT",
            line_item_updates={"TaxType": "INPUT"},
            human_steps=[
                "Open the bill in Xero.",
                "Edit each line item and set Tax Rate to Input Tax (INPUT).",
                "Verify the tax amount is correct.",
                "Save the bill.",
            ],
            rationale="Purchase bill has no VAT tax code. Setting TaxType to INPUT allows VAT recovery.",
            estimated_minutes=3,
        )

    if rule_id == "multi_tax_code_supplier" and suggested_code:
        return SuggestFixSuggestion(
            fix_strategy="standardise_tax_code",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType={suggested_code}",
            line_item_updates={"TaxType": suggested_code},
            human_steps=[
                "Open this transaction in Xero.",
                f"Edit line item(s) and set Tax Type to {suggested_code}.",
                "Save the change.",
            ],
            rationale=f"This supplier almost always uses tax code {suggested_code}. Standardising removes the inconsistency.",
            estimated_minutes=2,
        )

    if rule_id == "unexpected_tax_code" and suggested_code:
        return SuggestFixSuggestion(
            fix_strategy="correct_tax_code",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType={suggested_code}",
            line_item_updates={"TaxType": suggested_code},
            human_steps=[
                "Open this transaction in Xero.",
                f"Edit line item(s) and change Tax Type to {suggested_code}.",
                "Save the change.",
            ],
            rationale=f"Tax code {suggested_code} is expected for this account. The current code is an outlier.",
            estimated_minutes=2,
        )

    if rule_id == "multi_account_supplier" and suggested_code:
        return SuggestFixSuggestion(
            fix_strategy="standardise_account",
            xero_action=f"PUT /Invoices/{doc_id} line_items AccountCode={suggested_code}",
            line_item_updates={"AccountCode": suggested_code},
            human_steps=[
                "Open this transaction in Xero.",
                f"Edit line item(s) and change Account Code to {suggested_code}.",
                "Save the change.",
            ],
            rationale=f"This supplier is almost always posted to account {suggested_code}. This entry is the outlier.",
            estimated_minutes=2,
        )

    if rule_id == "future_dated":
        return SuggestFixSuggestion(
            fix_strategy="correct_date",
            xero_action=f"PUT /Invoices/{doc_id} {{\"Date\":\"<today>\"}}",
            field_updates={"Date": "today"},
            human_steps=[
                "Open this transaction in Xero.",
                "Change the Date to today or the correct transaction date.",
                "Save the change.",
            ],
            rationale="The transaction is future-dated. Correcting the date ensures it posts to the right period.",
            estimated_minutes=2,
        )

    if rule_id in (
        "old_unpaid_invoice", "old_unpaid_bill",
        "old_unsettled_sales_credit", "old_unsettled_purchase_credit",
        "opening_balance_difference",
        "invoice_or_direct_booking", "bill_or_direct_booking",
    ):
        noun_map = {
            "old_unpaid_invoice": "customer",
            "old_unpaid_bill": "supplier",
            "old_unsettled_sales_credit": "customer credit",
            "old_unsettled_purchase_credit": "supplier credit",
        }
        if rule_id in ("invoice_or_direct_booking", "bill_or_direct_booking"):
            vendor = tx.get("vendor_name") or "Unknown vendor"
            amount = tx.get("amount") or ""
            currency = tx.get("currency_code") or "GBP"
            inv_num = tx.get("invoice_number") or ""
            ref = tx.get("reference") or ""
            due = tx.get("due_date") or ""
            is_bill = rule_id == "bill_or_direct_booking"
            doc_type = "bill" if is_bill else "invoice"

            inv_line = f"Document number: {inv_num}" if inv_num else "Document number: Missing — obtain from supplier" if is_bill else "Document number: Missing — obtain from customer"
            ref_line = f"Reference: {ref}" if ref else None
            amount_line = f"Amount: {currency} {amount}" if amount else None
            due_line = f"Due date: {due}" if due else None

            details = [l for l in [inv_line, ref_line, amount_line, due_line] if l]

            steps = [
                f"Locate the original {doc_type} from {vendor}.",
                *details,
                f"Create a proper {doc_type.capitalize()} in Xero matching those details.",
                "Link or reconcile the bank payment to the new document.",
                "Void the direct coding if a separate entry was created.",
            ]
            rationale = (
                f"{vendor} — no {doc_type} reference found "
                f"({'document number: ' + inv_num if inv_num else 'document number missing'}). "
                f"Raise a proper {doc_type} so the transaction is fully traceable."
            )
        elif rule_id == "opening_balance_difference":
            steps = [
                "Run the Balance Sheet report in Xero as at the opening date.",
                "Compare with the prior accounting system / trial balance.",
                "Raise a journal entry to correct any difference.",
            ]
            rationale = "Opening balances do not reconcile. A correcting journal is required."
        else:
            contact = noun_map.get(rule_id, "contact")
            vendor = tx.get("vendor_name") or "Unknown"
            amount = tx.get("amount") or ""
            currency = tx.get("currency_code") or "GBP"
            inv_num = tx.get("invoice_number") or ""
            due = tx.get("due_date") or ""
            inv_label = f" ({inv_num})" if inv_num else ""
            amount_label = f" — {currency} {amount}" if amount else ""
            due_label = f" (due {due})" if due else ""
            steps = [
                f"Review {vendor}{inv_label}{amount_label}{due_label}.",
                f"Chase the {contact} for payment or confirm it is settled.",
                "If paid outside Xero: mark as paid with the correct date and account.",
                "If irrecoverable: create a credit note or bad-debt write-off journal.",
                "If raised in error: void the document.",
            ]
            rationale = (
                f"{vendor}{inv_label}{amount_label} has been outstanding"
                f"{due_label}. Chase, write off, or reconcile."
            )
        return SuggestFixSuggestion(
            fix_strategy="manual_review",
            xero_action="Manual steps required — see below.",
            human_steps=steps,
            rationale=rationale,
            estimated_minutes=10,
        )

    if rule_id == "sales_tax_on_bills":
        current = (first.get("current_code") or "").strip()
        return SuggestFixSuggestion(
            fix_strategy="correct_tax_code",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType=INPUT",
            line_item_updates={"TaxType": "INPUT"},
            human_steps=[
                "Open this bill in Xero.",
                f"Edit the line item — current tax code: {current}.",
                "Change Tax Type to INPUT (standard rated purchases).",
                "Save the bill.",
            ],
            rationale=(
                f"Sales tax code {current} is incorrectly applied to a purchase bill. "
                f"Change to INPUT so VAT can be reclaimed correctly."
            ),
            estimated_minutes=2,
        )

    if rule_id == "purchase_tax_on_invoices":
        current = (first.get("current_code") or "").strip()
        return SuggestFixSuggestion(
            fix_strategy="correct_tax_code",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType=OUTPUT",
            line_item_updates={"TaxType": "OUTPUT"},
            human_steps=[
                "Open this invoice in Xero.",
                f"Edit the line item — current tax code: {current}.",
                "Change Tax Type to OUTPUT (standard rated sales).",
                "Save the invoice.",
            ],
            rationale=(
                f"Purchase tax code {current} is incorrectly applied to a sales invoice. "
                f"Change to OUTPUT so VAT is correctly collected and reported."
            ),
            estimated_minutes=2,
        )

    if rule_id == "sales_tax_missing":
        return SuggestFixSuggestion(
            fix_strategy="add_sales_tax",
            xero_action=f"PUT /Invoices/{doc_id} line_items TaxType=OUTPUT",
            line_item_updates={"TaxType": "OUTPUT"},
            human_steps=[
                "Open this invoice in Xero.",
                "Edit each line item and set Tax Rate to Output Tax (OUTPUT).",
                "Verify the tax amount is correct before saving.",
                "Save the invoice.",
            ],
            rationale="Sales invoice has no VAT tax code. Setting TaxType to OUTPUT ensures VAT is collected and reported correctly.",
            estimated_minutes=3,
        )

    if rule_id in ("duplicate_contact", "contact_defaults", "inactive_contact"):
        vendor = tx.get("vendor_name") or "This contact"
        if rule_id == "duplicate_contact":
            steps = [
                f"Open Contacts in Xero and search for '{vendor}'.",
                "Identify the duplicate contact.",
                "Transfer any transactions from the duplicate to the original contact.",
                "Archive the duplicate contact.",
            ]
            rationale = f"'{vendor}' appears to be a duplicate. Merge by moving transactions to the original and archiving the duplicate."
        elif rule_id == "contact_defaults":
            steps = [
                f"Open the contact '{vendor}' in Xero.",
                "Go to the Financial Details tab.",
                "Set the default Purchase Account (for suppliers) or Sales Account (for customers).",
                "Set the default Tax Type to match this contact's usual VAT treatment.",
                "Save the contact.",
            ]
            rationale = f"'{vendor}' is missing default account or tax settings. Setting defaults ensures consistent coding on new transactions."
        else:  # inactive_contact
            steps = [
                f"Open the contact '{vendor}' in Xero.",
                "Confirm no pending invoices, bills, or outstanding balances.",
                "Click Archive to mark the contact as inactive.",
            ]
            rationale = f"'{vendor}' has had no transactions in 180 days. Archive it to keep your contact list clean."
        return SuggestFixSuggestion(
            fix_strategy="manual_review",
            xero_action="Manual steps required — see below.",
            human_steps=steps,
            rationale=rationale,
            estimated_minutes=5,
        )

    # wrong_direction_account and wrong_category → always LLM with COA context
    # so it picks the correct account from this org's actual Chart of Accounts.
    return None


def _infer_account_from_steps(steps: list[str]) -> Optional[str]:
    """Pull an account code from the LLM's human_steps if it mentioned one
    (e.g. 'Change Account Code to 461') even though it forgot line_item_updates."""
    import re
    pattern = re.compile(r"\b(\d{3,4})\b")
    for step in steps:
        m = pattern.search(step)
        if m:
            candidate = m.group(1)
            # Only trust 3-4 digit codes in the 400-500 range (UK expense accounts)
            if 400 <= int(candidate) <= 599:
                return candidate
    return None

def _primary_rule_id(
    result: Optional[dict[str, Any]],
    document_type: str,
) -> str:
    """Best-effort: prefer the first flagged rule's ``rule_id`` /
    ``issue_type``; fall back to the document type so the rules engine
    always gets *something* to anchor on."""
    flagged = (result or {}).get("flagged") or []
    if isinstance(flagged, list) and flagged:
        first = flagged[0] if isinstance(flagged[0], dict) else {}
        candidate = first.get("rule_id") or first.get("issue_type")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    rule_ids = (result or {}).get("rule_ids") or []
    if isinstance(rule_ids, list) and rule_ids:
        first = rule_ids[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return (document_type or "unknown").strip()


def _build_transaction_payload(row) -> dict[str, Any]:
    """Build the payload for the rules engine's suggest-fix endpoint.

    We surface key fields at the top level so the LLM prompt can reference
    them directly rather than fishing through the JSONB blob.
    """
    result = row.result or {}
    flagged = result.get("flagged") or []
    first_flag = flagged[0] if flagged else {}
    return {
        "transaction_id": str(row.document_id),
        "document_type": row.document_type,
        "messages": result.get("messages") or row.error_msgs,
        "result": result,
        # Surface these top-level so fix logic can use them directly
        "vendor_name": result.get("vendor_name") or "",
        "invoice_number": result.get("invoice_number") or "",
        "amount": result.get("amount") or "",
        "currency_code": result.get("currency_code") or "GBP",
        "due_date": result.get("due_date") or "",
        "reference": result.get("reference") or "",
        "current_account_code": first_flag.get("current_code") or "",
        "suggested_account_code": first_flag.get("suggested_code") or "",
        "issue_type": first_flag.get("issue_type") or "",
        "flagged_items": flagged,
    }


def _normalise_suggestion(raw: dict[str, Any]) -> SuggestFixSuggestion:
    """Defaults everywhere so the frontend never crashes on missing
    keys. Extra fields from the rules engine pass through via
    ``extra='allow'`` on the schema."""
    field_updates = raw.get("field_updates")
    if not isinstance(field_updates, dict):
        field_updates = None
    else:
        field_updates = {str(k): str(v) for k, v in field_updates.items()}

    try:
        estimated_minutes = int(raw.get("estimated_minutes") or 0)
    except (TypeError, ValueError):
        estimated_minutes = 0

    human_steps = raw.get("human_steps") or []
    if not isinstance(human_steps, list):
        human_steps = []

    return SuggestFixSuggestion(
        fix_strategy=str(raw.get("fix_strategy") or "manual_review"),
        xero_action=str(raw.get("xero_action") or ""),
        human_steps=[str(s) for s in human_steps],
        rationale=str(raw.get("rationale") or ""),
        estimated_minutes=estimated_minutes,
        field_updates=field_updates,
        target_transaction_id=(
            str(raw.get("target_transaction_id"))
            if raw.get("target_transaction_id")
            else None
        ),
    )
