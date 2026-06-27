"""Pre-ledger firewall — validate a single invoice before it hits Xero.

Deterministic field checks + a Groq categorisation when the data is
ambiguous (missing tax code or generic vendor name).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from app.core.config import settings
from app.modules.ai.client import get_groq
from app.schemas.transaction import InvoicePayload, InvoiceValidationResponse
from app.services.healthcheck.shared import _LLM_MIN_CONFIDENCE, _parse_json_object

logger = __import__("logging").getLogger("uvicorn.error")

async def validate_invoice(payload: InvoicePayload) -> InvoiceValidationResponse:
    """Deterministic rules + Groq categorization for a single invoice."""
    validation_errors = _run_invoice_rules(payload)

    needs_llm = (
        not payload.tax_code
        or _is_ambiguous_vendor(payload.vendor_name)
    )

    if needs_llm:
        category, confidence, reasoning = await _classify_with_groq(payload)
        if confidence < _LLM_MIN_CONFIDENCE:
            category = None
    else:
        category, confidence, reasoning = (
            None,
            1.0,
            "Vendor and tax code present; deterministic rules sufficient.",
        )

    return InvoiceValidationResponse(
        suggested_category=category,
        confidence_score=confidence,
        reasoning=reasoning,
        validation_errors=validation_errors,
    )


def _run_invoice_rules(payload: InvoicePayload) -> list[str]:
    errors: list[str] = []
    if not payload.invoice_number:
        errors.append("invoice_number is missing.")
    if not payload.tax_code:
        errors.append("tax_code is missing.")
    if payload.amount <= Decimal("0"):
        errors.append("amount must be greater than zero.")
    if not payload.vendor_name.strip():
        errors.append("vendor_name is blank.")
    if payload.date > date.today():
        errors.append("date is in the future.")
    return errors


def _is_ambiguous_vendor(vendor_name: str) -> bool:
    # Conservative heuristic: short or generic vendor names trigger LLM review.
    cleaned = vendor_name.strip().lower()
    if len(cleaned) < 4:
        return True
    return cleaned in {"misc", "miscellaneous", "n/a", "unknown", "vendor"}

_INVOICE_SYSTEM_PROMPT = (
    "You are a UK bookkeeping reviewer categorizing one invoice for a "
    "small-business ledger. Be concise and opinionated; no hedging. "
    "Return ONLY a JSON object with exactly these keys: "
    '{"suggested_category": string, "confidence_score": number between 0 and 1, '
    '"reasoning": string}. '
    "Rules: reasoning <= 140 chars; lead with the fact, not 'the transaction'; "
    "strip 'Ltd/Inc/LLC' from vendor names; no 'might/could/potentially'. "
    "No prose, no markdown fences."
)


async def _classify_with_groq(
    payload: InvoicePayload,
) -> tuple[Optional[str], float, str]:
    user_prompt = (
        f"Vendor: {payload.vendor_name}\n"
        f"Description: {payload.description}\n"
        f"Amount: {payload.amount}\n"
        f"Date: {payload.date.isoformat()}\n"
        f"Tax code: {payload.tax_code or 'MISSING'}\n"
        "Suggest the most appropriate accounting category."
    )
    try:
        client = get_groq()
        completion = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            max_tokens=400,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _INVOICE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = completion.choices[0].message.content or ""
        data = _parse_json_object(raw)
        return (
            data.get("suggested_category"),
            float(data.get("confidence_score", 0.0)),
            str(data.get("reasoning", "")),
        )
    except Exception:
        logger.exception("Groq invoice classification failed")
        return (
            None,
            0.0,
            "LLM unavailable; manual review required.",
        )
