"""Schemas for /api/v1/enrich-audit and /api/v1/suggest-fix.

These endpoints are the Django side of the health-check AI handoff: Django
sends the trapped rows from a batch audit, FastAPI does LLM enrichment and
writes results to Redis (async) or returns a fix suggestion (sync).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SeverityAI = Literal["critical", "high", "medium", "low"]


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _LooseBase(BaseModel):
    """Used for payloads where Django sends Xero shapes we don't fully model."""
    model_config = ConfigDict(extra="allow")


# ---------- /api/v1/enrich-audit ----------

class TrappedRow(_LooseBase):
    transaction_id: str = Field(..., min_length=1, max_length=128)
    rule_ids: list[str] = Field(default_factory=list)
    messages: str = ""
    transaction: dict[str, Any] = Field(default_factory=dict)
    flagged_items: list[dict[str, Any]] = Field(default_factory=list)


class EnrichAuditRequest(_StrictBase):
    batch_id: str = Field(..., min_length=1, max_length=128)
    company_id: str = Field(..., min_length=1, max_length=128)
    total_documents: int = Field(..., ge=0)
    trapped_rows: list[TrappedRow] = Field(default_factory=list, max_length=2000)


class EnrichAuditAccepted(_StrictBase):
    batch_id: str
    queued_rows: int
    status: Literal["queued", "disabled"]


# Stored under `health_check_ai:{transaction_id}` (one per trapped row).
class HealthCheckAIRecord(_StrictBase):
    explanation: str
    severity_ai: SeverityAI
    confidence: float = Field(..., ge=0.0, le=1.0)
    regulatory_ref: Optional[str] = None


# ---------- /api/v1/enrich-row (on-demand single row) ----------

class EnrichRowRequest(_StrictBase):
    """Single-row body used when Django needs an immediate enrichment
    (typically because the user just opened a trapped row whose AI
    insight hasn't landed from the background batch yet).
    """
    batch_id: Optional[str] = Field(default=None, max_length=128)
    row: TrappedRow


class EnrichRowResponse(_StrictBase):
    transaction_id: str
    status: Literal["enriched", "unavailable", "disabled"]
    record: Optional[HealthCheckAIRecord] = None


# Stored under `xero_historical_audit_batch:{batch_id}` hash, field `_meta.audit_summary`.
class AuditSummary(_StrictBase):
    summary: str
    top_themes: list[str] = Field(default_factory=list)
    suggested_cleanup_order: list[str] = Field(default_factory=list)


# ---------- /api/v1/suggest-fix ----------

class SuggestFixTransaction(_LooseBase):
    transaction_id: str = Field(..., min_length=1, max_length=128)
    document_type: Optional[str] = None
    rule_id: Optional[str] = None
    messages: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class SuggestFixRequest(_StrictBase):
    rule_id: str = Field(..., min_length=1, max_length=128)
    transaction: SuggestFixTransaction


class SuggestFixResponse(_StrictBase):
    fix_strategy: str
    xero_action: str
    human_steps: list[str] = Field(default_factory=list)
    rationale: str
    estimated_minutes: int = Field(..., ge=0, le=480)
    # The Xero document the caller should actually PUT against. Usually the
    # input transaction's id; for duplicate-void cases it's the sibling
    # invoice (the newer one, which we want voided to keep the original).
    target_transaction_id: Optional[str] = None
    # Structured map of top-level Xero HEADER fields → new value
    # (e.g. {"Status": "VOIDED"}). Lets Django apply the fix without
    # parsing xero_action. Null when the fix is line-item-only, needs
    # a credit note, or requires manual steps.
    field_updates: Optional[dict[str, Any]] = None
    # Structured map of Xero LINE-ITEM fields → new value, applied to
    # every line on the invoice (e.g. {"AccountCode": "489",
    # "TaxType": "INPUT"}). Null when the fix is header-only.
    line_item_updates: Optional[dict[str, Any]] = None
