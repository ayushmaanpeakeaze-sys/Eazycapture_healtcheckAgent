"""Thin async HTTP wrapper around the existing ``/api/v1/suggest-fix``.

Yes, the rules engine lives in the same FastAPI process today — so an
in-process import would technically work. We keep it behind ``httpx``
on purpose so the engine can move to a separate service without changing
callers — the HTTP contract is the seam. Fail-open by design.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("eazycapture.ai_client")


async def suggest_fix(
    rule_id: str,
    transaction: dict[str, Any],
    base_url: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Call POST /api/v1/suggest-fix. Returns the suggestion dict,
    or ``None`` on any failure (timeout / HTTP error / decode error /
    feature-gate-disabled). The caller decides whether to surface a
    fallback shape or treat as ``available=false``."""
    url = base_url or settings.HEALTHCHECK_AI_SUGGEST_FIX_URL
    payload = {"rule_id": rule_id, "transaction": transaction}
    timeout_s = max(1, settings.HEALTHCHECK_AI_TIMEOUT_MS / 1000)

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError:
        logger.exception("[SuHe][AIClient] suggest-fix transport error url=%s", url)
        return None

    if resp.status_code != 200:
        logger.warning(
            "[SuHe][AIClient] suggest-fix HTTP %s :: %s",
            resp.status_code, resp.text[:200],
        )
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.exception("[SuHe][AIClient] suggest-fix non-JSON body")
        return None
    if not isinstance(data, dict):
        return None
    return data
