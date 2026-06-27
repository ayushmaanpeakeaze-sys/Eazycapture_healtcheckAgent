"""LLM enrichment for trapped rows + per-row fix suggestions.

Both routes are gated by ``settings.HEALTHCHECK_AI_ENABLED`` and fail-open:
when disabled, ``enrich-audit`` returns ``{"status":"disabled"}`` and
``suggest-fix`` returns 503 — so Django's existing fail-open paths stay
intact during rollout / kill-switch scenarios.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.modules.ai.schemas import (
    EnrichAuditAccepted,
    EnrichAuditRequest,
    EnrichRowRequest,
    EnrichRowResponse,
    SuggestFixRequest,
    SuggestFixResponse,
)
from app.modules.ai import insight_service

logger = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["enrichment"])


@router.post(
    "/enrich-audit",
    response_model=EnrichAuditAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Async LLM enrichment for trapped rows; results written to Redis.",
)
async def enrich_audit(payload: EnrichAuditRequest) -> EnrichAuditAccepted:
    if not settings.HEALTHCHECK_AI_ENABLED:
        return EnrichAuditAccepted(
            batch_id=payload.batch_id, queued_rows=0, status="disabled",
        )
    try:
        queued = await insight_service.enrich_audit_async(payload)
    except Exception as exc:
        logger.exception("enrich_audit endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue audit enrichment.",
        ) from exc
    return EnrichAuditAccepted(
        batch_id=payload.batch_id, queued_rows=queued, status="queued",
    )


@router.post(
    "/enrich-row",
    response_model=EnrichRowResponse,
    status_code=status.HTTP_200_OK,
    summary="On-demand LLM enrichment for a single trapped row (synchronous).",
)
async def enrich_row(payload: EnrichRowRequest) -> EnrichRowResponse:
    """Called by Django when the user opens a row whose AI insight hasn't
    landed from the background batch yet. ~1-2s response, writes through
    to Redis so subsequent polls hit the cache.
    """
    if not settings.HEALTHCHECK_AI_ENABLED:
        return EnrichRowResponse(
            transaction_id=payload.row.transaction_id,
            status="disabled",
        )
    try:
        record = await insight_service.enrich_row_sync(
            payload.row, batch_id=payload.batch_id,
        )
    except Exception as exc:
        logger.exception("enrich_row endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enrich row.",
        ) from exc
    return EnrichRowResponse(
        transaction_id=payload.row.transaction_id,
        status="enriched" if record else "unavailable",
        record=record,
    )


@router.post(
    "/suggest-fix",
    response_model=SuggestFixResponse,
    status_code=status.HTTP_200_OK,
    summary="Synchronous LLM-generated fix recommendation for a single flagged row.",
)
async def suggest_fix(payload: SuggestFixRequest) -> SuggestFixResponse:
    if not settings.HEALTHCHECK_AI_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Health-check AI is disabled (HEALTHCHECK_AI_ENABLED=false).",
        )
    try:
        return await insight_service.suggest_fix(payload)
    except Exception as exc:
        logger.exception("suggest_fix endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate fix suggestion.",
        ) from exc
