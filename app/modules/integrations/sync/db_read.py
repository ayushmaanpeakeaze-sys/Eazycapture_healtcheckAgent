"""Read helpers the audit uses INSTEAD of live Xero fetches (AUDIT_SOURCE=db).

These return the RAW Xero dicts byte-for-byte as the live fetch did, so the
audit's existing reshape / mapping / checks are 100% unchanged — "only the data
source changes". Pure synchronous SQL (the audit runs in Celery on a sync
``Session``); no Nango, no network.

Mirrors the live fetch surface:
  ``read_documents``  ≡ ``tasks._pull_xero_documents`` → (docs, bank_txns) with
                        ``_IsReconciled`` injected from the synced payments.
  ``read_raw``        → one entity's raw rows (accounts / tax_rates / contacts /
                        organisation), for the caller to map exactly as before.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.integrations.sync.models import XeroDocument


def read_raw(db: Session, company_id, entity: str) -> list[dict[str, Any]]:
    """Every stored raw Xero record for one company+entity (sync order: the
    JSONB payload exactly as Xero returned it)."""
    rows = db.scalars(
        select(XeroDocument.raw_json).where(
            XeroDocument.company_id == company_id,
            XeroDocument.entity == entity,
        )
    ).all()
    return [r for r in rows if isinstance(r, dict)]


def read_organisation(db: Session, company_id) -> dict[str, Any]:
    """The single synced Organisation record ({} if none)."""
    rows = read_raw(db, company_id, "organisation")
    return rows[0] if rows else {}


def _reconciled_invoice_ids(payments: list[dict[str, Any]]) -> set[str]:
    """IDs of invoices/bills whose payment is BANK MATCHED — identical logic to
    ``tasks._reconciled_invoice_ids`` so the reconciled flag matches the live
    path exactly."""
    out: set[str] = set()
    for p in payments or []:
        if not isinstance(p, dict) or not p.get("IsReconciled"):
            continue
        inv = p.get("Invoice") or {}
        inv_id = (inv.get("InvoiceID") or "").strip()
        if inv_id:
            out.add(inv_id)
    return out


def read_documents(
    db: Session, company_id
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mirror of ``_pull_xero_documents``: ``(docs, bank_txns)`` where ``docs``
    is invoices + credit notes (each tagged ``_IsReconciled`` from the synced
    payments) and ``bank_txns`` is the raw Money In/Out rows."""
    invoices = read_raw(db, company_id, "invoice")
    credit_notes = read_raw(db, company_id, "credit_note")
    payments = read_raw(db, company_id, "payment")
    bank_txns = read_raw(db, company_id, "bank_transaction")

    reconciled_ids = _reconciled_invoice_ids(payments)
    docs = invoices + credit_notes
    for raw in docs:
        if isinstance(raw, dict):
            doc_id = (raw.get("InvoiceID") or raw.get("CreditNoteID") or "").strip()
            raw["_IsReconciled"] = doc_id in reconciled_ids
    return docs, bank_txns


def has_synced_documents(db: Session, company_id) -> bool:
    """True once an initial sync has populated invoices for this company — the
    gate for using the DB path vs falling back to a live fetch."""
    count = db.scalar(
        select(func.count())
        .select_from(XeroDocument)
        .where(
            XeroDocument.company_id == company_id,
            XeroDocument.entity == "invoice",
        )
    )
    return bool(count and count > 0)


__all__ = [
    "read_raw",
    "read_organisation",
    "read_documents",
    "has_synced_documents",
]
