"""Resets Demo Co to a known-good state for sir's walkthrough.

Run with:

    python -m scripts.reset_demo
    # or
    make reset-demo

Steps:

1. Wipe Demo Co's ``health_check_result`` + ``audit_batch`` rows.
2. Clear the matching Redis keys (``xero_historical_audit_batch:*``
   created for Demo Co, and any ``health_check_ai:*`` for the
   document ids about to be re-trapped).
3. Re-run the audit and wait for it to finish.
4. Fire the re-enrich sweep so every row ends up with a populated
   ``ai`` annotation, working around Groq's free-tier TPM cap.

End state: 15 trapped rows in Demo Co, all enriched.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Optional
from uuid import UUID

import httpx
import redis as sync_redis
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.db import SyncSessionLocal
from app.modules.healthcheck.models import (
    AuditBatch,
    HealthCheckResult,
    Invoice,
)
from app.modules.healthcheck.seed_data import DEMO_CO_ID


API_BASE = "http://127.0.0.1:8001"
WAIT_AUDIT_SECONDS = 60
WAIT_REENRICH_SECONDS = 60
POLL_INTERVAL = 1.5


def _step(num: int, message: str) -> None:
    print(f"[{num}] {message}", flush=True)


def wipe_demo_rows() -> None:
    """Delete health_check_result + audit_batch rows for Demo Co."""
    with SyncSessionLocal() as db:
        db.execute(
            delete(HealthCheckResult)
            .where(HealthCheckResult.company_id == DEMO_CO_ID)
        )
        db.execute(
            delete(AuditBatch).where(AuditBatch.company_id == DEMO_CO_ID)
        )
        db.commit()


def list_demo_invoice_ids() -> list[UUID]:
    with SyncSessionLocal() as db:
        rows = db.execute(
            select(Invoice.id).where(Invoice.company_id == DEMO_CO_ID)
        ).scalars().all()
    return list(rows)


def wipe_demo_redis_keys(invoice_ids: list[UUID]) -> int:
    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    deleted = 0
    try:
        # Per-row AI enrichments for every Demo Co document.
        if invoice_ids:
            keys = [f"health_check_ai:{i}" for i in invoice_ids]
            deleted += r.delete(*keys)
        # Audit-batch progress hashes. We don't track which batch_ids
        # belong to Demo Co (Redis hash only carries the id in its
        # ``_meta`` payload), so we walk the prefix and delete the
        # matching ones explicitly to avoid nuking other companies'
        # in-flight audits.
        for key in r.scan_iter(match="xero_historical_audit_batch:*"):
            raw = r.hget(key, "_meta")
            if not raw:
                continue
            try:
                meta = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if meta.get("company_id") == str(DEMO_CO_ID):
                deleted += r.delete(key)
    finally:
        r.close()
    return deleted


def dispatch_audit() -> str:
    resp = httpx.post(
        f"{API_BASE}/api/v1/health/sync-xero-history/{DEMO_CO_ID}/",
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    return str(body["batch_id"])


def wait_audit_completed(batch_id: str) -> dict:
    deadline = time.time() + WAIT_AUDIT_SECONDS
    last: Optional[dict] = None
    while time.time() < deadline:
        resp = httpx.get(
            f"{API_BASE}/api/v1/health/sync-xero-history-status/{batch_id}/",
            timeout=10,
        )
        resp.raise_for_status()
        last = resp.json()
        if last.get("status") in {"completed", "failed"}:
            return last
        time.sleep(POLL_INTERVAL)
    raise SystemExit(
        f"audit did not finish within {WAIT_AUDIT_SECONDS}s; last state={last}"
    )


def dispatch_reenrich() -> dict:
    resp = httpx.post(
        f"{API_BASE}/api/v1/health/re-enrich/?company_id={DEMO_CO_ID}",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def wait_all_enriched(expected: int) -> tuple[int, int]:
    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        with SyncSessionLocal() as db:
            doc_ids = db.execute(
                select(HealthCheckResult.document_id).where(
                    HealthCheckResult.company_id == DEMO_CO_ID,
                    HealthCheckResult.kind == "post_ledger",
                    HealthCheckResult.status == "blocked",
                )
            ).scalars().all()
        keys = [f"health_check_ai:{d}" for d in doc_ids]
        if not keys:
            return 0, 0

        deadline = time.time() + WAIT_REENRICH_SECONDS
        present = 0
        while time.time() < deadline:
            values = r.mget(keys)
            present = sum(
                1 for v in values
                if v not in (None, "") and (
                    not isinstance(v, str) or v.strip() != ""
                )
            )
            if present >= expected:
                return present, len(keys)
            time.sleep(POLL_INTERVAL)
        return present, len(keys)
    finally:
        r.close()


def main() -> int:
    print(
        f"[reset_demo] Demo Co = {DEMO_CO_ID}  api={API_BASE}",
        flush=True,
    )

    _step(1, "wiping DB rows for Demo Co…")
    wipe_demo_rows()

    _step(2, "wiping Demo Co's Redis keys…")
    invoice_ids = list_demo_invoice_ids()
    deleted = wipe_demo_redis_keys(invoice_ids)
    print(f"      deleted {deleted} Redis key(s)", flush=True)

    _step(3, "dispatching audit…")
    batch_id = dispatch_audit()
    print(f"      batch_id={batch_id}", flush=True)

    _step(4, f"waiting for audit to complete (up to {WAIT_AUDIT_SECONDS}s)…")
    final_status = wait_audit_completed(batch_id)
    print(
        f"      status={final_status['status']} "
        f"total={final_status['total']} "
        f"trapped={final_status['trapped']} "
        f"new_trapped={final_status['new_trapped']}",
        flush=True,
    )
    if final_status["status"] != "completed":
        print("      audit failed; aborting reset.", flush=True)
        return 1

    _step(5, "dispatching re-enrich sweep…")
    reenrich = dispatch_reenrich()
    print(
        f"      task_id={reenrich['task_id']} "
        f"eligible_rows={reenrich['eligible_rows']}",
        flush=True,
    )

    _step(6, "waiting for AI annotations to land…")
    expected_total = final_status["trapped"]
    present, total = wait_all_enriched(expected_total)
    print(
        f"      enriched {present}/{total} trapped rows", flush=True,
    )

    print(
        f"\n[reset_demo] DONE — Demo Co has {final_status['trapped']} "
        f"trapped row(s), {present}/{total} with AI annotations.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
