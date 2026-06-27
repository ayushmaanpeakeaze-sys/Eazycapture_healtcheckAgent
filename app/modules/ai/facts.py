"""facts.py — the GROUNDING CONTRACT.

`build_row_facts` produces the exact, complete set of facts the LLM is allowed
to see for one flagged row. The LLM phrases these facts; it must never see less
(over-truncated → it invents the missing bit) or more (unrelated data → it
drifts). Every figure the explanation might cite belongs IN here, and nowhere
else does the LLM get data.

This is also the one file to read to know "what does the AI actually know about
a flagged transaction?".
"""
from __future__ import annotations

import json
from typing import Optional

from app.modules.ai.templates import get_context

_PURCHASE_DOC_TYPES = {"ACCPAY", "ACCPAYCREDIT"}
_SALES_DOC_TYPES = {"ACCREC", "ACCRECCREDIT"}

# Generous safety cap (was 400). We pass the COMPLETE flagged detail so the
# model is grounded; this only trims pathologically large payloads to protect
# the token budget — normal rows pass through structured + untouched.
_FLAGGED_DETAIL_MAX_CHARS = 2000


def extract_doc_type(payload: object) -> Optional[str]:
    """Best-effort pull of the Xero document type from a free-shaped dict."""
    if not isinstance(payload, dict):
        return None
    for key in ("type", "Type", "document_type", "DocumentType"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def contact_noun(doc_type: Optional[str]) -> str:
    """UK terminology: supplier for ACCPAY, customer for ACCREC, else contact."""
    if doc_type in _PURCHASE_DOC_TYPES:
        return "supplier"
    if doc_type in _SALES_DOC_TYPES:
        return "customer"
    return "contact"


def pull(keys: list[str], *sources: Optional[dict]) -> Optional[object]:
    """First non-empty value found at any of `keys` across `sources` (in order)."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                value = source[key]
                if value not in (None, ""):
                    return value
    return None


def _safe_flagged_detail(value: object) -> object:
    """Pass the structured flagged detail through untouched; only fall back to a
    truncated string if it is pathologically large (protects the token budget)."""
    raw = json.dumps(value, default=str)
    if len(raw) <= _FLAGGED_DETAIL_MAX_CHARS:
        return value
    return raw[:_FLAGGED_DETAIL_MAX_CHARS]


def build_row_facts(row, idx: int) -> dict:
    """The grounding contract for one trapped row → the dict handed to the LLM.

    Pure (no I/O, no LLM) and fully testable. ``idx`` is the position in the
    chunk so the model can match its result back by id.
    """
    doc_type = extract_doc_type(row.transaction)
    tx = row.transaction if isinstance(row.transaction, dict) else {}
    primary_rule = row.rule_ids[0] if row.rule_ids else ""
    so_what, solution = get_context(primary_rule)
    return {
        "id": idx,
        "doc_type": doc_type or "unknown",
        "contact_noun": contact_noun(doc_type),
        "vendor": tx.get("vendor_name") or tx.get("contact_name") or "Unknown",
        "amount": tx.get("amount") or tx.get("total") or "unknown",
        "currency": tx.get("currency_code") or "GBP",
        "account_code": tx.get("current_account_code") or tx.get("account_code") or "unknown",
        "invoice_number": tx.get("invoice_number") or None,
        "status": tx.get("status") or "unknown",
        "rule_ids": row.rule_ids,
        "deterministic_finding": (row.messages or "")[:300],
        # COMPLETE structured detail (the grounding) — not aggressively truncated.
        "flagged_detail": _safe_flagged_detail(row.flagged_items),
        # WHY this rule exists + the fix, so the explanation is grounded in real
        # accounting impact, not a restatement of the technical flag.
        "business_impact": so_what,
        "recommended_action": solution,
    }
