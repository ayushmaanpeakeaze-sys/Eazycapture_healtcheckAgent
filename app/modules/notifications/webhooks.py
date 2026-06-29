"""Inbound email delivery webhook — ``POST /api/v1/webhooks/email``.

Email providers (Resend, SendGrid, Postmark, SES…) POST delivery events
here when a message is delivered, bounces, or is marked as spam. We
normalize the payload, then update notification_log + the affected user's
``email_status`` so the team list can flag a bad address.

Provider-agnostic: understands Resend, SendGrid and a generic shape. Add a
new provider by extending :func:`_extract_events` — nothing else changes.

Auth: a shared secret (``EMAIL_WEBHOOK_SECRET``) via ``X-Webhook-Secret``
header or ``?secret=`` query. Empty secret → accept-with-warning (dev),
mirroring the Nango webhook so local testing isn't blocked.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.modules.notifications.persistence import apply_delivery_event

logger = logging.getLogger("eazycapture.notifications.webhook")

router = APIRouter(tags=["webhooks"])


@router.post("/api/v1/webhooks/email", summary="Receive email delivery/bounce events")
async def email_webhook(
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None),
    secret: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Any:
    # --- auth ---
    expected = settings.EMAIL_WEBHOOK_SECRET
    if expected:
        if (x_webhook_secret or secret) != expected:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid webhook secret."},
            )
    else:
        logger.warning(
            "EMAIL_WEBHOOK_SECRET unset — accepting unverified email webhook",
        )

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Body is not valid JSON."},
        )

    events = _extract_events(payload)
    applied = 0
    for ev in events:
        if await apply_delivery_event(
            db,
            email=ev.get("email"),
            event=ev.get("event", ""),
            provider=ev.get("provider"),
            message_id=ev.get("message_id"),
        ):
            applied += 1

    return {"received": len(events), "applied": applied}


# --------------------------------------------------------------------------
# Provider payload normalization → [{email, event, message_id, provider}, ...]
# --------------------------------------------------------------------------

def _extract_events(payload: Any) -> list[dict[str, Optional[str]]]:
    """Flatten any supported provider payload into normalized events."""
    # SendGrid posts a JSON array of event objects.
    if isinstance(payload, list):
        out: list[dict[str, Optional[str]]] = []
        for item in payload:
            if isinstance(item, dict):
                out.append(_from_sendgrid(item))
        return [e for e in out if e.get("email") or e.get("message_id")]

    if not isinstance(payload, dict):
        return []

    # Resend / generic single-event shape: {"type": "email.bounced", "data": {...}}
    if "type" in payload and "data" in payload and isinstance(payload["data"], dict):
        return [_from_resend(payload)]

    # Generic: {"events": [ {email,event,message_id}, ... ]}
    if isinstance(payload.get("events"), list):
        out = []
        for item in payload["events"]:
            if isinstance(item, dict):
                out.append(_from_generic(item))
        return [e for e in out if e.get("email") or e.get("message_id")]

    # Generic single: {"email": "...", "event": "...", "message_id": "..."}
    one = _from_generic(payload)
    return [one] if (one.get("email") or one.get("message_id")) else []


def _from_sendgrid(item: dict) -> dict[str, Optional[str]]:
    return {
        "email": item.get("email"),
        "event": item.get("event", ""),
        "message_id": item.get("sg_message_id") or item.get("smtp-id"),
        "provider": "sendgrid",
    }


def _from_resend(payload: dict) -> dict[str, Optional[str]]:
    # type "email.bounced" → event "bounced"
    raw_type = str(payload.get("type", ""))
    event = raw_type.split(".", 1)[1] if "." in raw_type else raw_type
    data = payload.get("data") or {}
    to = data.get("to")
    if isinstance(to, list):
        to = to[0] if to else None
    return {
        "email": data.get("email") or to,
        "event": event,
        "message_id": data.get("email_id") or data.get("id"),
        "provider": "resend",
    }


def _from_generic(item: dict) -> dict[str, Optional[str]]:
    return {
        "email": item.get("email") or item.get("recipient"),
        "event": item.get("event") or item.get("status", ""),
        "message_id": item.get("message_id") or item.get("id"),
        "provider": item.get("provider", "generic"),
    }
