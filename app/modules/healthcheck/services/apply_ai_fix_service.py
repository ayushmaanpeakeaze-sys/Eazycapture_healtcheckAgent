"""Glue between an AI suggestion and the resolve pipeline.

Responsibilities, in order:

1. Load the trapped row (or 404).
2. Get a suggestion — caller-provided override or fresh from the
   rules engine via :class:`SuggestFixService`.
3. Detect target redirect — duplicates flag the row clicked but the
   AI may want to act on the sibling. We look up the sibling's
   trapped row by ``document_id`` + ``company_id`` and reassign.
4. Extract ``field_updates`` — prefer structured, fall back to
   parsing the ``xero_action`` string.
5. Apply field-name aliases (``TaxCode`` → ``TaxType``).
6. Reject placeholder values for ``InvoiceNumber`` (frontend should
   prompt the user for a real number instead of writing "FIXME-001"
   to Xero).
7. Delegate to :class:`ResolveService` — which writes to Xero via Nango
   when the org is connected, otherwise records the intent.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import Company, HealthCheckResult
from app.modules.healthcheck.repository import HealthCheckResultRepository
from app.modules.healthcheck.schemas import (
    ResolveResponse,
    SuggestFixSuggestion,
)
from app.modules.healthcheck.services.resolve_service import (
    ALLOWED_LINE_ITEM_FIELDS,
    ALLOWED_UPDATE_FIELDS,
    ResolveService,
)
from app.modules.healthcheck.services.suggest_fix_service import (
    SuggestFixService,
)
from app.modules.healthcheck.xero_links import xero_deep_link

logger = logging.getLogger("eazycapture.apply_ai_fix")

_FIELD_ALIASES: dict[str, str] = {
    "TaxCode": "TaxType",
}

_TARGET_ID_RE = re.compile(
    r"/(?:Invoices|Bills|CreditNotes)/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# ``PUT /Invoices/{id} { Key: Value, OtherKey: "quoted value" }``
_FIELD_PAIR_RE = re.compile(
    r"""([A-Za-z][A-Za-z0-9_]*)\s*:\s*("([^"]*)"|'([^']*)'|([^,}\s]+))""",
)

PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "FIXME", "TODO", "PLACEHOLDER", "PENDING",
    "UNKNOWN", "MISSING", "TBD", "???", "AUTO-",
)


class ApplyAiFixService:
    def __init__(
        self,
        db: AsyncSession,
        suggest_service: SuggestFixService,
        resolve_service: ResolveService,
    ) -> None:
        self._db = db
        self._suggest = suggest_service
        self._resolve = resolve_service
        self._repo = HealthCheckResultRepository(db)

    async def apply(
        self,
        *,
        row_id: UUID,
        company_id: UUID,
        suggestion_override: Optional[SuggestFixSuggestion] = None,
        user_id: Optional[UUID] = None,
    ) -> ResolveResponse:
        # 1. Row check (404 if cross-tenant / missing).
        row = await self._repo.find_by_id(row_id, company_id)
        if row is None:
            return _error(
                row_id=row_id,
                document_id=row_id,  # caller's id since row is unknown
                error_code="ROW_NOT_FOUND",
                error_detail="Trapped row not found for this company.",
                xero_url=None,
            )

        company = await self._db.get(Company, company_id)
        shortcode = (
            (company.xero_shortcode or "").strip() or None
            if company is not None else None
        )
        xero_url = xero_deep_link(row.document_type, row.document_id, shortcode)

        # 2. Suggestion: override beats fresh call (saves an LLM hop).
        if suggestion_override is not None:
            suggestion_dict = suggestion_override.model_dump()
            suggestion = suggestion_override
            available = True
        else:
            suggest_response = await self._suggest.get_suggestion(
                row_id, company_id,
            )
            if not suggest_response.available:
                return _error(
                    row_id=row.id,
                    document_id=row.document_id,
                    error_code="AI_UNAVAILABLE",
                    error_detail=(
                        suggest_response.reason
                        or "Rules engine returned no suggestion."
                    ),
                    xero_url=xero_url,
                )
            suggestion = suggest_response.suggestion
            suggestion_dict = suggestion.model_dump()
            available = True
        del available  # silence linters; ``suggestion`` is what matters

        # 3. Target-redirect for duplicate-style suggestions.
        target_doc_id = _resolve_target_document_id(suggestion_dict)
        effective_row = row
        if target_doc_id and target_doc_id != str(row.document_id):
            sibling = await self._find_trapped_by_doc_id(
                target_doc_id, company_id,
            )
            if sibling is None:
                return _error(
                    row_id=row.id,
                    document_id=row.document_id,
                    error_code="AI_TARGET_NOT_TRAPPED",
                    error_detail=(
                        f"AI suggested fixing sibling document "
                        f"{target_doc_id} but no trapped row exists for it."
                    ),
                    xero_url=xero_url,
                )
            logger.info(
                "[SuHe][ApplyAI] redirect row=%s → sibling=%s "
                "(duplicate target_transaction_id=%s)",
                row.id, sibling.id, target_doc_id,
            )
            effective_row = sibling
            xero_url = xero_deep_link(
                sibling.document_type, sibling.document_id, shortcode,
            )

        # 4-5. Field updates: prefer structured, fall back to parse, alias.
        field_updates = _resolve_field_updates(suggestion_dict)
        if not field_updates:
            return _error(
                row_id=effective_row.id,
                document_id=effective_row.document_id,
                error_code="NO_FIELD_UPDATES",
                error_detail=(
                    "AI suggestion had no usable field_updates. "
                    "Frontend should show manual-fix steps."
                ),
                xero_url=xero_url,
                ai_fix_strategy=suggestion.fix_strategy,
            )

        # 6. Placeholder guard (only InvoiceNumber today — extend list
        # in PLACEHOLDER_MARKERS as the rules engine emits more).
        placeholder = _detect_placeholder(field_updates)
        if placeholder is not None:
            field, value = placeholder
            return _error(
                row_id=effective_row.id,
                document_id=effective_row.document_id,
                error_code="MANUAL_FIX_REQUIRED",
                error_detail=(
                    f"AI suggested placeholder value for {field!r}: "
                    f"{value!r}. Bookkeeper must supply the real value."
                ),
                xero_url=xero_url,
                ai_fix_strategy=suggestion.fix_strategy,
            )

        # 7. Split + reject if neither header nor line-item allow-list
        # matched (e.g. AI hallucinated an unsupported key).
        has_header = any(k in ALLOWED_UPDATE_FIELDS for k in field_updates)
        has_lines = any(k in ALLOWED_LINE_ITEM_FIELDS for k in field_updates)
        if not has_header and not has_lines:
            return _error(
                row_id=effective_row.id,
                document_id=effective_row.document_id,
                error_code="NO_SUPPORTED_FIELDS",
                error_detail=(
                    "AI field_updates contain no Xero-supported keys."
                ),
                xero_url=xero_url,
                ai_fix_strategy=suggestion.fix_strategy,
            )

        # 8. Delegate to ResolveService (writes to Xero via Nango when connected).
        resolve_response = await self._resolve.resolve(
            row_id=effective_row.id,
            company_id=company_id,
            field_updates=field_updates,
            resolution_notes=(
                f"Auto-applied AI fix: {suggestion.fix_strategy}"
            ),
            resolved_by_user_id=user_id,
            ai_applied=True,
            ai_fix_strategy=suggestion.fix_strategy,
        )
        return resolve_response

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _find_trapped_by_doc_id(
        self,
        document_id: str,
        company_id: UUID,
    ) -> Optional[HealthCheckResult]:
        try:
            doc_uuid = UUID(document_id)
        except (TypeError, ValueError):
            return None
        stmt = (
            select(HealthCheckResult)
            .where(
                HealthCheckResult.company_id == company_id,
                HealthCheckResult.document_id == doc_uuid,
                HealthCheckResult.kind == "post_ledger",
                HealthCheckResult.status == "blocked",
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()


# ----------------------- module-level helpers -----------------------

def _parse_xero_action(xero_action: str) -> dict[str, str]:
    """Tease ``{Key: Value, ...}`` pairs out of an ``xero_action`` hint.

    Tolerant of quoted/unquoted values and trailing whitespace. Returns
    ``{}`` on a malformed input rather than raising.
    """
    if not xero_action:
        return {}
    brace_start = xero_action.find("{")
    brace_end = xero_action.rfind("}")
    if brace_start == -1 or brace_end == -1 or brace_end <= brace_start:
        return {}
    body = xero_action[brace_start + 1:brace_end]
    out: dict[str, str] = {}
    for match in _FIELD_PAIR_RE.finditer(body):
        key = match.group(1)
        value = (
            match.group(3) if match.group(3) is not None
            else match.group(4) if match.group(4) is not None
            else (match.group(5) or "")
        )
        out[key] = value.strip()
    return out


def _normalise_field_names(updates: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in updates.items():
        key = _FIELD_ALIASES.get(key, key)
        out[key] = str(value) if value is not None else ""
    return out


def _resolve_field_updates(suggestion: dict[str, Any]) -> dict[str, str]:
    structured = suggestion.get("field_updates")
    if isinstance(structured, dict) and structured:
        return _normalise_field_names(structured)
    parsed = _parse_xero_action(suggestion.get("xero_action") or "")
    return _normalise_field_names(parsed) if parsed else {}


def _resolve_target_document_id(suggestion: dict[str, Any]) -> Optional[str]:
    target = suggestion.get("target_transaction_id")
    if isinstance(target, str) and target.strip():
        return target.strip()
    match = _TARGET_ID_RE.search(suggestion.get("xero_action") or "")
    if match:
        return match.group(1)
    return None


def _detect_placeholder(
    field_updates: dict[str, str],
) -> Optional[tuple[str, str]]:
    invoice_number = field_updates.get("InvoiceNumber")
    if invoice_number is None:
        return None
    upper = invoice_number.upper()
    for marker in PLACEHOLDER_MARKERS:
        if marker in upper:
            return ("InvoiceNumber", invoice_number)
    return None


def _error(
    *,
    row_id: UUID,
    document_id: UUID,
    error_code: str,
    error_detail: str,
    xero_url: Optional[str],
    ai_fix_strategy: Optional[str] = None,
) -> ResolveResponse:
    return ResolveResponse(
        row_id=row_id,
        document_id=document_id,
        resolved=False,
        applied_updates={},
        xero_url=xero_url,
        ai_applied=False,
        ai_fix_strategy=ai_fix_strategy,
        error_code=error_code,
        error_detail=error_detail,
    )
