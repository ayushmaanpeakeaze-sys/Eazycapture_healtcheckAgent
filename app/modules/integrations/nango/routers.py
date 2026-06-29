"""HTTP surface for Nango-related operations.

Two routes:

* ``POST /api/v1/integrations/nango/connect-session/`` — kicks off the
  frontend OAuth popup. Returns 503 when Nango is disabled.
* ``POST /api/v1/webhooks/nango`` — receives Nango webhooks. For
  ``auth.creation`` events we look up the connection's tenant id and
  persist it on the :class:`Company` row identified by the
  ``end_user`` payload.

HMAC verification uses the ``NANGO_WEBHOOK_SECRET``. When the secret
is empty we accept the webhook but log a warning so the missing
verification is obvious in production logs.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.modules.auth.models import User, UserCompanyAccess
from app.modules.healthcheck.models import Company
from app.modules.healthcheck.services.audit_service import AuditService
from app.modules.integrations.nango.service import NangoService
from app.modules.integrations.service import IntegrationService

logger = logging.getLogger("eazycapture.nango.router")

router = APIRouter(tags=["nango"])

_LOG_TAG = "[SuHe][Nango]"


# ---------------------------------------------------------------------
# Connect-session
# ---------------------------------------------------------------------

@router.post(
    "/api/v1/integrations/nango/connect-session/",
    status_code=status.HTTP_200_OK,
    summary="Open a Nango Connect session for the frontend OAuth popup.",
)
async def create_connect_session(
    provider: str = Query("xero"),
    user: CurrentUser = Depends(get_current_user),
):
    """Open a Nango Connect session for the signed-in accountant.

    The Nango ``end_user.id`` is the AUTHENTICATED user's id — so when the
    auth.creation webhook fires, we know which accountant connected and can
    link every org they bring in to that account. In demo mode (no real
    user) we fall back to ``NANGO_USER_ID`` so demos still work.
    """
    nango = NangoService()
    if not nango.is_available():
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": (
                    "Nango is not configured on this deployment "
                    "(NANGO_SECRET_KEY unset)."
                ),
            },
        )
    # Authenticated user → use their UUID. Demo mode (user_id is None) →
    # fall back to the configured demo user id.
    if user.user_id is not None:
        end_user_id = str(user.user_id)
    else:
        end_user_id = (settings.NANGO_USER_ID or "demo-user").strip()

    payload = await nango.create_xero_connect_session(end_user_id=end_user_id)
    if payload is None:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Nango refused the connect-session call."},
        )
    return payload


@router.post(
    "/api/v1/integrations/nango/sync-connections/",
    status_code=status.HTTP_200_OK,
    summary="Webhook-free org creation: build the signed-in user's Xero orgs "
    "from their live connection. Call right after the OAuth popup closes.",
)
async def sync_connections(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Make 'connect → org appears' work WITHOUT the auth.creation webhook.

    The webhook needs a public URL (ngrok in local/demo) which is fragile. This
    does the same job on demand: find the user's newest live Xero connection,
    enumerate its orgs, and create/link/sync/audit each — by reusing the exact
    webhook handler. The frontend calls this on the Nango ``connect`` event, so
    the org shows up even if the webhook never fires. Idempotent (upserts), so
    it's safe alongside the webhook.
    """
    if not NangoService().is_available():
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Nango is not configured on this deployment."},
        )
    integration = IntegrationService()
    try:
        live = await integration.find_live_xero_connection()
    except Exception:  # noqa: BLE001 — detection is best-effort
        live = None
    if not live:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "detail": "No live Xero connection found — connect Xero first."
            },
        )
    connection_id, _tenant = live

    # Reuse the EXACT webhook logic: upsert one Company per org, link the user,
    # set the user's connection, kick off initial sync + auto-audit.
    creation_payload: dict[str, Any] = {
        "connectionId": connection_id,
        "endUser": {
            "endUserId": str(user.user_id) if user.user_id else None,
        },
    }
    await _handle_auth_creation(creation_payload, db)

    orgs = (
        await db.execute(
            select(Company.id, Company.name).where(
                Company.nango_connection_id == connection_id,
                Company.is_active.is_(True),
            )
        )
    ).all()
    return {
        "status": "ok",
        "connection_id": connection_id,
        "orgs": [{"company_id": str(cid), "name": name} for cid, name in orgs],
    }


# ---------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------

@router.post(
    "/api/v1/webhooks/nango",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive Nango webhooks (auth.creation, etc).",
)
async def nango_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_nango_signature: Optional[str] = Header(default=None),
):
    raw_body = await request.body()

    if not _verify_signature(raw_body, x_nango_signature):
        # Wrong signature is a real attacker signal — refuse explicitly.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid webhook signature."},
        )

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Webhook body was not JSON."},
        )

    event_type, operation = _classify_event(payload)
    if event_type == "auth" and operation in {"creation", "created"}:
        await _handle_auth_creation(payload, db)
    else:
        logger.info(
            "%s webhook received type=%s operation=%s (no-op)",
            _LOG_TAG, event_type, operation,
        )

    return {"accepted": True}


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _verify_signature(body: bytes, signature: Optional[str]) -> bool:
    """Return True when the signature matches the configured secret.

    Skip-with-warning when the secret is unset (demo default) so the
    webhook handler still functions during demos without forcing
    HMAC config.
    """
    secret = (settings.NANGO_WEBHOOK_SECRET or "").strip()
    if not secret:
        logger.warning(
            "%s webhook signature verification skipped — "
            "NANGO_WEBHOOK_SECRET is unset.",
            _LOG_TAG,
        )
        return True
    if not signature:
        logger.warning("%s webhook missing X-Nango-Signature header", _LOG_TAG)
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        logger.warning("%s webhook signature mismatch", _LOG_TAG)
        return False
    return True


def _classify_event(payload: dict[str, Any]) -> tuple[str, str]:
    event_type = str(
        payload.get("type")
        or payload.get("event")
        or "",
    ).strip().lower()
    operation = str(
        payload.get("operation")
        or payload.get("action")
        or "",
    ).strip().lower()
    return event_type, operation


def _user_id_from_payload(payload: dict[str, Any]) -> Optional[UUID]:
    """The Nango ``end_user.id`` is the AUTHENTICATED accountant's UUID
    (set in connect-session). Returns None for demo connects where the
    end_user id is a non-UUID label like ``test_ayushmaan_singh`` — in that
    case we still create the orgs, just without a user link."""
    end_user = payload.get("endUser") or payload.get("end_user") or {}
    if isinstance(end_user, dict):
        candidate = end_user.get("endUserId") or end_user.get("id")
        if candidate:
            try:
                return UUID(str(candidate))
            except (TypeError, ValueError):
                return None
    return None


async def _handle_auth_creation(
    payload: dict[str, Any],
    db: AsyncSession,
) -> None:
    """On Xero connect: enumerate EVERY org the grant covers and create one
    Company per org, linked to the connecting accountant, then auto-audit
    each. One accountant's single OAuth grant fans out to all their clients.
    """
    connection_id = str(
        payload.get("connectionId") or payload.get("connection_id") or ""
    ).strip()
    if not connection_id:
        logger.warning("%s auth.creation missing connectionId", _LOG_TAG)
        return

    # The accountant who connected (None in demo — orgs still created,
    # just not access-linked; the synthetic demo admin sees all anyway).
    user_id = _user_id_from_payload(payload)
    user: Optional[User] = None
    if user_id is not None:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            logger.warning(
                "%s auth.creation end_user=%s is not a known user — "
                "creating orgs without a user link", _LOG_TAG, user_id,
            )

    # Enumerate every Xero org this connection can reach.
    integration = IntegrationService()
    tenants = await integration.list_tenants(connection_id)
    if not tenants:
        logger.warning(
            "%s auth.creation connection=%s returned no tenants",
            _LOG_TAG, connection_id,
        )
        return

    new_company_ids: list[UUID] = []
    for t in tenants:
        tenant_id = t["tenant_id"]
        tenant_name = t["tenant_name"]

        # Upsert Company keyed on (connection_id, tenant_id).
        company = (
            await db.execute(
                select(Company).where(
                    Company.nango_connection_id == connection_id,
                    Company.xero_tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        # The org belongs to the connecting accountant's firm (None in demo).
        firm_id = user.firm_id if user is not None else None
        if company is None:
            company = Company(
                name=tenant_name,
                firm_id=firm_id,
                nango_connection_id=connection_id,
                xero_tenant_id=tenant_id,
                is_active=True,
            )
            db.add(company)
            await db.flush()  # get company.id
            new_company_ids.append(company.id)
        else:
            company.name = tenant_name or company.name
            company.is_active = True
            if company.firm_id is None and firm_id is not None:
                company.firm_id = firm_id

        # Link the connecting accountant to this org (idempotent).
        if user is not None:
            exists = (
                await db.execute(
                    select(UserCompanyAccess.id).where(
                        UserCompanyAccess.user_id == user.id,
                        UserCompanyAccess.company_id == company.id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(UserCompanyAccess(user_id=user.id, company_id=company.id))

    if user is not None:
        user.nango_connection_id = connection_id

    # Concurrent / duplicate webhook deliveries can both pass the SELECT and
    # both INSERT the same (connection_id, tenant_id) — the partial unique
    # index then makes one commit raise. Treat that as an idempotent no-op:
    # the concurrent winner already created the orgs + dispatched audits.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info(
            "%s concurrent duplicate delivery for connection=%s — "
            "idempotent no-op (orgs handled by the concurrent winner)",
            _LOG_TAG, connection_id,
        )
        return

    # Initial sync (the "first sync"): full-pull each new org's Xero data
    # into the DB so later audits read from our tables. Fire-and-forget; the
    # first auto-audit below may run before it finishes and simply falls back to
    # a live fetch until the sync lands — the audit always works either way.
    from app.modules.integrations.sync.tasks import sync_company_task

    for cid in new_company_ids:
        try:
            sync_company_task.delay(str(cid), full=True)
        except Exception:
            logger.exception(
                "%s initial sync dispatch failed for company=%s", _LOG_TAG, cid,
            )

    # Auto-audit each newly-created org. dispatch_audit just inserts a batch
    # row + enqueues the Celery worker (.delay) — fast, non-blocking.
    audit = AuditService(db)
    try:
        for cid in new_company_ids:
            try:
                await audit.dispatch_audit(cid)
            except Exception:
                logger.exception(
                    "%s auto-audit dispatch failed for company=%s", _LOG_TAG, cid,
                )
    finally:
        await audit.close()

    logger.info(
        "%s connect: connection=%s user=%s orgs=%d new=%d audits_dispatched=%d",
        _LOG_TAG, connection_id, user_id, len(tenants),
        len(new_company_ids), len(new_company_ids),
    )


# NOTE: the old single-org helpers `_set_connection_metadata` and
# `_extract_xero_tenant_id` were removed with the multi-org rewrite. They
# stored one tenantId in Nango connection metadata so pre-built Actions
# could resolve the org — a last-write-wins approach that is wrong for a
# multi-org connection. All reads now go through the tenant-scoped proxy
# (which passes nango-proxy-xero-tenant-id per call), so neither is needed.
