"""Celery tasks for the DB-backed Xero sync — the three sync modes.

  Initial sync     — first connect → full pull (``sync_company_task`` with no
                     watermark yet → every entity full).
  Daily auto-sync  — nightly beat (``sync_all_companies_task``) fans out one
                     ``sync_company_task`` per connected org; each entity runs
                     incrementally off its watermark.
  Manual sync      — the dashboard "Refresh Data" button hits an endpoint that
                     enqueues ``sync_company_task`` for that one org.

Async engine work is bridged with ``asyncio.run`` (the engine uses an
``AsyncSession`` for concurrent upserts); enumeration uses the sync session like
the rest of the Celery layer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionLocal, SyncSessionLocal
from app.modules.healthcheck.models import Company
from app.modules.integrations.sync.engine import SyncEngine

logger = logging.getLogger("uvicorn.error")


async def _run_company_sync(
    company_id: UUID,
    *,
    force_full: bool,
    entities: Optional[list[str]],
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        company = (
            await db.execute(select(Company).where(Company.id == company_id))
        ).scalar_one_or_none()
        if company is None:
            return {"status": "error", "error": "company not found"}
        if not company.nango_connection_id or not company.xero_tenant_id:
            return {"status": "skipped", "error": "company not connected"}

        engine = SyncEngine()
        results = await engine.sync_company(
            db, company, entities=entities, force_full=force_full,
        )
        return {
            "status": "ok",
            "company_id": str(company_id),
            "entities": {
                e: {
                    "status": r.status,
                    "records": r.records,
                    "mode": r.mode,
                    "error": r.error,
                }
                for e, r in results.items()
            },
            "total_records": sum(r.records for r in results.values()),
        }


@celery_app.task(name="healthcheck.sync_xero", bind=False, max_retries=0)
def sync_company_task(
    company_id: str,
    full: bool = False,
    entities: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Sync one company. ``full=True`` forces a full re-pull (ignores watermark);
    otherwise each entity decides full-vs-incremental from its own watermark."""
    try:
        cid = UUID(str(company_id))
    except (ValueError, TypeError):
        return {"status": "error", "error": f"bad company_id {company_id!r}"}
    result = asyncio.run(
        _run_company_sync(cid, force_full=full, entities=entities)
    )
    logger.info(
        "[Sync] task done company=%s status=%s records=%s",
        company_id, result.get("status"), result.get("total_records"),
    )
    return result


@celery_app.task(name="healthcheck.sync_all_xero", bind=False, max_retries=0)
def sync_all_companies_task() -> dict[str, Any]:
    """Nightly auto-sync: enqueue an incremental ``sync_company_task`` for every
    connected, active org. Fans out (one task each) so a slow org can't block
    the rest and the worker pool parallelises them."""
    with SyncSessionLocal() as db:
        company_ids = db.scalars(
            select(Company.id).where(
                Company.nango_connection_id.isnot(None),
                Company.xero_tenant_id.isnot(None),
                Company.is_active.is_(True),
            )
        ).all()

    for cid in company_ids:
        sync_company_task.delay(str(cid))
    logger.info("[Sync] nightly auto-sync enqueued for %d companies", len(company_ids))
    return {"status": "ok", "enqueued": len(company_ids)}


__all__ = ["sync_company_task", "sync_all_companies_task"]
