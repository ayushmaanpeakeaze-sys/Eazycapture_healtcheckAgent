"""Aggregator: composes per-domain routers under /api/v1.

Each domain lives in ``app.api.routers.*``. Keep this file thin — it's only
the mount point. New endpoints go in a router module, not here.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import health_check, validation
# AI enrichment/insight router now lives in the consolidated AI module.
from app.modules.ai.router import router as enrichment_router

router = APIRouter(prefix="/api/v1")
router.include_router(validation.router)
router.include_router(health_check.router)
router.include_router(enrichment_router)
