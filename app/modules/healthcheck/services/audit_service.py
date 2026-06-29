"""Audit dispatch + status service.

* ``dispatch_audit`` — creates an ``audit_batch`` row, seeds the Redis
  meta hash, enqueues the Celery task, returns ``batch_id``. Always
  returns within a few ms; never blocks on the audit.
* ``get_status``     — pure Redis read used by the polling endpoint.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import redis.asyncio as async_redis
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.healthcheck.models import AuditBatch, Company
from app.modules.healthcheck.schemas import (
    AuditStatusResponse,
    DispatchAuditResponse,
)

logger = logging.getLogger("eazycapture.audit")

BATCH_HASH_PREFIX = "xero_historical_audit_batch"
META_FIELD = "_meta"
AUDIT_SUMMARY_FIELD = "_meta.audit_summary"
AI_ENRICHED_COUNT_FIELD = "_meta.ai_enriched_count"


def batch_key(batch_id: UUID | str) -> str:
    """Stable Redis key for the audit-batch meta hash."""
    return f"{BATCH_HASH_PREFIX}:{batch_id}"


class AuditService:
    """Coordinates the dispatch + status endpoints. Holds an async DB
    session for the dispatch path; status only touches Redis."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._redis: Optional[async_redis.Redis] = None

    # --------------- internal helpers ------------------------------

    def _get_redis(self) -> async_redis.Redis:
        if self._redis is None:
            self._redis = async_redis.from_url(
                settings.REDIS_URL, decode_responses=True,
            )
        return self._redis

    async def _seed_meta_hash(
        self,
        batch_id: UUID,
        company_id: UUID,
    ) -> None:
        started_at = datetime.now(timezone.utc).isoformat()
        meta = {
            "company_id": str(company_id),
            "batch_id": str(batch_id),
            "status": "in_progress",
            "stage": "dispatched",
            "stage_label": "Audit queued…",
            "started_at": started_at,
            "total": 0,
            "trapped": 0,
            "new_trapped": 0,
        }
        redis = self._get_redis()
        key = batch_key(batch_id)
        await redis.hset(key, META_FIELD, json.dumps(meta))
        await redis.expire(key, settings.HEALTHCHECK_BATCH_HASH_TTL_SECONDS)

    # --------------- public API ------------------------------------

    async def dispatch_audit(
        self,
        company_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        scope: str = "full",
    ) -> DispatchAuditResponse:
        """Begin an audit for one company, optionally scoped to a date period.

        ``date_from`` / ``date_to`` (inclusive) limit which transactions the
        audit considers — used by the frontend's Period selector. ``None``
        for both audits everything. Idempotent at the batch level (each call
        gets a fresh ``batch_id``)."""
        # 1. Company existence — multi-tenant guard already runs at the
        # route layer, but a second check at the service layer keeps the
        # service callable from non-HTTP contexts (e.g. a future cron job).
        exists = await self.db.execute(
            select(Company.id).where(Company.id == company_id)
        )
        if exists.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Unknown company.",
            )

        batch_id = uuid4()

        # 2. Persist the audit_batch row first so a Celery crash before
        # Redis seed doesn't leave the DB inconsistent. The session is
        # already inside a transaction (auto-begun by the previous
        # SELECT); commit explicitly rather than opening a nested one.
        batch = AuditBatch(
            id=batch_id,
            company_id=company_id,
            status="in_progress",
            total=0,
            trapped=0,
            new_trapped=0,
        )
        self.db.add(batch)
        await self.db.commit()

        # 3. Seed Redis meta — frontend can poll immediately.
        await self._seed_meta_hash(batch_id, company_id)

        # 4. Enqueue the worker task. Imported inline so importing this
        # service module doesn't drag in Celery at app boot. Dates are passed
        # as ISO strings (Celery args must be JSON-serialisable).
        from app.modules.healthcheck.tasks import historical_audit_task
        historical_audit_task.delay(
            str(batch_id),
            str(company_id),
            date_from.isoformat() if date_from else None,
            date_to.isoformat() if date_to else None,
            scope,
        )

        logger.info(
            "[SuHe][Audit] dispatched batch_id=%s company_id=%s period=%s..%s",
            batch_id, company_id, date_from, date_to,
        )
        return DispatchAuditResponse(batch_id=batch_id)

    async def get_status(self, batch_id: UUID) -> AuditStatusResponse:
        """Status snapshot for one batch. Pure Redis — single HGETALL."""
        redis = self._get_redis()
        key = batch_key(batch_id)
        raw = await redis.hgetall(key)
        if not raw:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Batch not found (expired or never started).",
            )

        meta = _parse_json(raw.get(META_FIELD)) or {}
        audit_summary = _parse_json(raw.get(AUDIT_SUMMARY_FIELD))

        try:
            ai_count_raw = raw.get(AI_ENRICHED_COUNT_FIELD)
            ai_enriched_count = (
                int(ai_count_raw) if ai_count_raw is not None else None
            )
        except (TypeError, ValueError):
            ai_enriched_count = None

        trapped = int(meta.get("trapped", 0) or 0)
        ai_summary_ready = audit_summary is not None
        ai_enrichment_complete = (
            ai_summary_ready
            and (
                trapped == 0
                or (
                    ai_enriched_count is not None
                    and ai_enriched_count >= trapped
                )
            )
        )

        return AuditStatusResponse(
            batch_id=batch_id,
            status=meta.get("status", "in_progress"),
            stage=meta.get("stage"),
            stage_label=meta.get("stage_label"),
            total=int(meta.get("total", 0) or 0),
            trapped=trapped,
            new_trapped=int(meta.get("new_trapped", 0) or 0),
            started_at=_parse_dt(meta.get("started_at")),
            fetched_at=_parse_dt(meta.get("fetched_at")),
            completed_at=_parse_dt(meta.get("completed_at")),
            error=meta.get("error"),
            audit_summary=audit_summary,
            ai_summary_ready=ai_summary_ready,
            ai_enriched_count=ai_enriched_count,
            ai_enrichment_complete=ai_enrichment_complete,
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


def _parse_json(raw: Optional[str]) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[SuHe][Audit] failed to decode meta JSON: %r", raw[:80])
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
