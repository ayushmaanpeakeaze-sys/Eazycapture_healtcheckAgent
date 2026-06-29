"""Re-point a company at the CURRENT live Nango→Xero connection, then (optionally)
run a duplicates audit.

Why this exists: on the Nango free plan the Xero integration uses Nango's shared
OAuth app, which doesn't grant ``offline_access`` → no refresh token → the access
token dies every ~30 min. Reconnecting in the Nango dashboard fixes it, but each
reconnect mints a BRAND-NEW connection id + tenant id, so the company row goes
stale. This script auto-detects the live connection and repoints the company.

    # after reconnecting in the Nango dashboard:
    python -m scripts.refresh_xero_connection            # repoint only
    python -m scripts.refresh_xero_connection --audit    # repoint + run duplicates audit

By default it targets the single company that already has a Nango connection set;
pass --company <uuid> to target a specific one.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SyncSessionLocal
from app.modules.healthcheck.models import Company
from app.modules.integrations.nango.client import NangoClient

API_BASE = "http://127.0.0.1:8001"


async def _live_xero_connection() -> tuple[str, str] | None:
    """Return (connection_id, tenant_id) of the newest live Xero connection, or
    None if there isn't one / it can't read invoices."""
    c = NangoClient()
    if not c._is_enabled():
        print("[refresh] Nango not configured (no secret key).")
        return None
    body = await c._send(
        "GET", f"{c._base_url}/connection",
        headers={"Authorization": f"Bearer {c._secret_key}"},
    )
    conns = (body or {}).get("connections", body if isinstance(body, list) else [])
    xero = [cn for cn in conns
            if (cn.get("provider_config_key") or cn.get("provider")) == settings.NANGO_XERO_INTEGRATION_ID]
    if not xero:
        print(f"[refresh] No Xero connection found in Nango ({len(conns)} total). "
              f"Reconnect in the Nango dashboard first.")
        return None
    # Newest first (a fresh reconnect is the one we want).
    xero.sort(key=lambda cn: cn.get("created") or cn.get("created_at") or "", reverse=True)
    cid = xero[0].get("connection_id") or xero[0].get("id")
    full = await c.get_connection(cid, settings.NANGO_XERO_INTEGRATION_ID)
    tenant = (full or {}).get("connection_config", {}).get("tenant_id")
    if not tenant:
        print(f"[refresh] Connection {cid} has no tenant_id yet.")
        return None
    # Best-effort liveness check (don't block the repoint if it's about to expire).
    try:
        await c.proxy_get(
            connection_id=cid, provider_config_key=settings.NANGO_XERO_INTEGRATION_ID,
            endpoint="api.xro/2.0/Invoices", tenant_id=tenant, params={"page": 1},
        )
        print(f"[refresh] Live connection {cid} (Xero responding)")
    except Exception as exc:  # noqa: BLE001
        print(f"[refresh] connection {cid} reachable but Xero call failed "
              f"({type(exc).__name__}) — repointing anyway; reconnect if the audit fails.")
    return cid, tenant


def _target_company(explicit: str | None) -> Company | None:
    with SyncSessionLocal() as db:
        if explicit:
            return db.get(Company, UUID(explicit))
        rows = db.scalars(
            select(Company).where(Company.nango_connection_id.isnot(None))
        ).all()
        if len(rows) == 1:
            return rows[0]
        if not rows:
            print("[refresh] No company has a Nango connection set — pass --company <uuid>.")
        else:
            print("[refresh] Multiple Nango-connected companies — pass --company <uuid>:")
            for r in rows:
                print(f"    {r.id}  {r.name}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="company UUID (default: the single Nango-connected one)")
    ap.add_argument("--audit", action="store_true", help="dispatch a duplicates audit after repointing")
    args = ap.parse_args()

    live = asyncio.run(_live_xero_connection())
    if not live:
        return 1
    conn_id, tenant_id = live

    co = _target_company(args.company)
    if co is None:
        return 1
    cid = co.id
    with SyncSessionLocal() as db:
        row = db.get(Company, cid)
        before = (row.nango_connection_id, row.xero_tenant_id)
        row.nango_connection_id = conn_id
        row.xero_tenant_id = tenant_id
        db.commit()
    print(f"[refresh] {co.name} ({cid})")
    print(f"[refresh]   connection: {before[0]} → {conn_id}")
    print(f"[refresh]   tenant    : {before[1]} → {tenant_id}")

    if args.audit:
        try:
            resp = httpx.post(
                f"{API_BASE}/api/v1/health/sync-xero-history/{cid}/?scope=duplicates",
                timeout=10,
            )
            resp.raise_for_status()
            print(f"[refresh] duplicates audit dispatched: batch {resp.json().get('batch_id')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[refresh] could not dispatch audit ({exc}); start the API + worker.")
    print("[refresh] DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
