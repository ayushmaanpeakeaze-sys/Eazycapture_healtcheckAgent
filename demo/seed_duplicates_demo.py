"""Seed FAKE duplicate-invoice data so the frontend Duplicate page has something
to show — covering every scenario in sir's logic.

It builds fake transactions, runs them through the REAL duplicate engine
(``_find_duplicate_bills``), and persists the resulting flags as
``health_check_result`` trapped rows under a dedicated demo company. So what the
frontend renders is exactly what a real audit would produce (real match_reasons,
real tiers/confidence) — just on hand-made transactions.

Run:  python -m demo.seed_duplicates_demo
Idempotent — re-running wipes this company's prior seed and re-creates it.

Confidence model (sir's rules; default window = 0 = SAME issue date):
  1. Same invoice number + ref + amount, same day ....... 99% HIGH   (textbook)
     + one PAID & BANK-RECONCILED, one OUTSTANDING ....... ⚠️ high risk
  2. Different invoice number, SAME day, rest same ....... 95% HIGH   + "2 distinct docs?"
  3. Same reference + amount, no invoice number .......... 90% HIGH
  4. Duplicate credit note (credit↔credit) .............. 99% HIGH
  5. Cross-contact (2 merged customer records) .......... 99% HIGH   + ⚠️ risk
  6. Weak: no reference, no number (amount+customer+day) . 75% review (MEDIUM)
  7. Recurring monthly subscription (wide window) ....... 45% review (LOW)
  8. Different AMOUNT + different number (part-invoices) . 65% review (MEDIUM)
  9. Different number with a DAY GAP (window widened) .... 70% review (MEDIUM)
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.db import SyncSessionLocal
from app.modules.healthcheck.models import AuditBatch, Company, HealthCheckResult
from app.schemas.transaction import BatchTransaction, FlaggedIssue
from app.services.healthcheck.audit_settings import AuditSettings, DEFAULT_SETTINGS
from app.services.healthcheck.deterministic import (
    _build_contact_alias,
    _find_duplicate_bills,
)

COMPANY_NAME = "Duplicates Demo (seed)"
KIND_POST_LEDGER = "post_ledger"
STATUS_BLOCKED = "blocked"
TARGET_LEDGER = "xero"


def _cid() -> str:
    return str(uuid.uuid4())


def _tx(
    *, contact, ref, amount, d, typ="ACCREC", vendor, num=None,
    status="AUTHORISED", paid="0", reconciled=None, line_desc=None, posted=None,
) -> BatchTransaction:
    amt = Decimal(amount)
    paid_d = Decimal(paid)
    return BatchTransaction(
        transaction_id=str(uuid.uuid4()),
        date=d,
        description=line_desc or (ref or "Services"),
        amount=amt,
        vendor_name=vendor,
        type=typ,
        contact_id=contact,
        reference=ref,
        invoice_number=num,
        status=status,
        amount_paid=paid_d,
        amount_due=amt - paid_d,
        reconciled=reconciled,
        due_date=date(d.year, d.month, 28),
        posted_date=posted or d,
        currency_code="GBP",
        line_items=[],
    )


def build_scenarios() -> tuple[list[BatchTransaction], list[FlaggedIssue]]:
    """Returns (all_transactions, all_flags)."""
    txns: list[BatchTransaction] = []
    flags: list[FlaggedIssue] = []

    # === 1. Textbook duplicate — same number + one PAID & RECONCILED, one OUT ==
    c1 = _cid()
    s1 = [
        _tx(contact=c1, ref="PO-7781", num="INV-3300", amount="2400.00",
            d=date(2026, 3, 5), vendor="Northgate Solutions",
            line_desc="Project Atlas", status="PAID", paid="2400.00",
            reconciled=True),                                   # PAID + bank matched
        _tx(contact=c1, ref="PO-7781", num="INV-3300", amount="2400.00",
            d=date(2026, 3, 5), vendor="Northgate Solutions",
            line_desc="Project Atlas", status="AUTHORISED", paid="0"),  # OUTSTANDING
    ]

    # === 2. Different invoice number, SAME day, rest same → 95% + hint =========
    c2 = _cid()
    s2 = [
        _tx(contact=c2, ref="BL-4471", num="INV-3301", amount="1850.00",
            d=date(2026, 4, 10), vendor="Brightline Media Ltd",
            line_desc="Quarterly retainer"),
        _tx(contact=c2, ref="BL-4471", num="INV-3302", amount="1850.00",
            d=date(2026, 4, 10), vendor="Brightline Media Ltd",
            line_desc="Quarterly retainer"),
    ]

    # === 3. Same reference + amount, NO invoice number → 90% ===================
    c3 = _cid()
    s3 = [
        _tx(contact=c3, ref="OSD-88", amount="430.00", d=date(2026, 4, 2),
            typ="ACCPAY", vendor="Office Supplies Direct",
            line_desc="Printer cartridges", status="PAID", paid="430.00",
            reconciled=True),
        _tx(contact=c3, ref="OSD-88", amount="430.00", d=date(2026, 4, 2),
            typ="ACCPAY", vendor="Office Supplies Direct",
            line_desc="Printer cartridges", status="AUTHORISED", paid="0"),
    ]

    # === 4. Duplicate sales credit note → 99% =================================
    c4 = _cid()
    s4 = [
        _tx(contact=c4, ref="CRN-12", num="CN-1043", amount="300.00",
            d=date(2026, 3, 20), typ="ACCRECCREDIT", vendor="Pixel Studios",
            line_desc="Refund — cancelled order"),
        _tx(contact=c4, ref="CRN-12", num="CN-1043", amount="300.00",
            d=date(2026, 3, 20), typ="ACCRECCREDIT", vendor="Pixel Studios",
            line_desc="Refund — cancelled order"),
    ]

    # === 5. Cross-contact (2 merged supplier records) + risk → 99% ============
    c5a, c5b = _cid(), _cid()
    s5 = [
        _tx(contact=c5a, ref="ACM-55", num="ACM-55", amount="720.00",
            d=date(2026, 1, 14), typ="ACCPAY", vendor="Acme Trading",
            line_desc="Consumables", status="PAID", paid="720.00",
            reconciled=True),
        _tx(contact=c5b, ref="ACM-55", num="ACM-55", amount="720.00",
            d=date(2026, 1, 14), typ="ACCPAY", vendor="Acme Trading Ltd",
            line_desc="Consumables", status="AUTHORISED", paid="0"),
    ]
    alias = _build_contact_alias([[c5a, c5b]])

    # === 6. Weak review — no reference, no invoice number → 75% MEDIUM =========
    c6 = _cid()
    s6 = [
        _tx(contact=c6, ref=None, amount="640.00", d=date(2026, 5, 9),
            vendor="Vertex Design Co", line_desc="Brand assets"),
        _tx(contact=c6, ref=None, amount="640.00", d=date(2026, 5, 9),
            vendor="Vertex Design Co", line_desc="Brand assets"),
    ]

    default_block = s1 + s2 + s3 + s4 + s5 + s6
    txns += default_block
    flags += _find_duplicate_bills(default_block, alias, DEFAULT_SETTINGS)

    # === 7. Recurring monthly subscription → 45% LOW (needs a wide window) =====
    c7 = _cid()
    s7 = [
        _tx(contact=c7, ref="CH-SUB", num=f"INV-70{m:02d}", amount="99.00",
            d=date(2026, m, 10), vendor="Cloud Hosting Co",
            line_desc="Monthly hosting")
        for m in (1, 2, 3, 4)
    ]
    txns += s7
    flags += _find_duplicate_bills(
        s7, None, AuditSettings.from_config({"duplicate_days_window": 35}),
    )

    # === 8. Different AMOUNT + number (part-invoices) → 65% MEDIUM ============
    # Surfaces only when 'require same amount' is OFF (the genuinely ambiguous
    # "could be 2 distinct documents" case).
    c8 = _cid()
    s8 = [
        _tx(contact=c8, ref="HC-2026", num="INV-4001", amount="5000.00",
            d=date(2026, 2, 1), vendor="Harbour Construction",
            line_desc="Phase 1 works", status="PAID", paid="5000.00",
            reconciled=True),
        _tx(contact=c8, ref="HC-2026", num="INV-4002", amount="15000.00",
            d=date(2026, 2, 1), vendor="Harbour Construction",
            line_desc="Phase 2 works", status="AUTHORISED", paid="0"),
    ]
    txns += s8
    flags += _find_duplicate_bills(
        s8, None, AuditSettings.from_config({"duplicate_require_same_amount": False}),
    )

    # === 9. Different number with a DAY GAP → 70% MEDIUM (window widened) ======
    c9 = _cid()
    s9 = [
        _tx(contact=c9, ref="GAP-1", num="INV-8001", amount="700.00",
            d=date(2026, 6, 1), vendor="Lumen Partners", line_desc="Workshop"),
        _tx(contact=c9, ref="GAP-1", num="INV-8002", amount="700.00",
            d=date(2026, 6, 3), vendor="Lumen Partners", line_desc="Workshop"),
    ]
    txns += s9
    flags += _find_duplicate_bills(
        s9, None, AuditSettings.from_config({"duplicate_days_window": 2}),
    )

    return txns, flags


def main() -> None:
    txns, flags = build_scenarios()
    by_id = {t.transaction_id: t for t in txns}
    now = datetime.now(timezone.utc)

    grouped: dict[str, list[FlaggedIssue]] = defaultdict(list)
    for f in flags:
        grouped[f.transaction_id].append(f)

    distinct_contacts = len({t.contact_id for t in txns})

    with SyncSessionLocal() as db:
        company = (
            db.query(Company).filter(Company.name == COMPANY_NAME).one_or_none()
        )
        if company is None:
            company = Company(
                id=uuid.uuid4(), name=COMPANY_NAME, is_active=True,
                xero_shortcode="demo-dupes",
            )
            db.add(company)
            db.flush()
        else:
            db.query(HealthCheckResult).filter(
                HealthCheckResult.company_id == company.id
            ).delete()
            db.query(AuditBatch).filter(
                AuditBatch.company_id == company.id
            ).delete()
            db.flush()

        batch = AuditBatch(
            id=uuid.uuid4(), company_id=company.id, status="completed",
            total=len(txns), trapped=len(grouped), new_trapped=len(grouped),
            contacts_total=distinct_contacts, started_at=now, completed_at=now,
        )
        db.add(batch)

        for tx_id, tx_flags in grouped.items():
            tx = by_id[tx_id]
            rule_ids = [f.issue_type for f in tx_flags]
            messages = [f.message for f in tx_flags if f.message]
            joined = " | ".join(messages)
            result_payload = {
                "flagged": [f.model_dump(mode="json") for f in tx_flags],
                "rule_ids": rule_ids,
                "messages": joined,
                "target_ledger": TARGET_LEDGER,
                "audit_batch_id": str(batch.id),
                "vendor_name": tx.vendor_name,
                "invoice_number": tx.invoice_number,
                "amount": str(tx.amount),
                "currency_code": tx.currency_code or "GBP",
                "invoice_date": tx.date.isoformat(),
                "posted_date": tx.posted_date.isoformat() if tx.posted_date else None,
                "due_date": tx.due_date.isoformat() if tx.due_date else None,
                "amount_due": str(tx.amount_due) if tx.amount_due is not None else None,
                "amount_paid": str(tx.amount_paid) if tx.amount_paid is not None else None,
                "reconciled": tx.reconciled,            # bank-matched flag
                "details": (tx.description or "").strip()[:200] or None,
                "reference": (tx.description or "").strip()[:200] or None,
                "xero_reference": (tx.reference or "").strip() or None,
                "invoice_status": (tx.status or "").strip().upper() or None,
            }
            db.add(HealthCheckResult(
                id=uuid.uuid4(),
                company_id=company.id,
                document_id=uuid.UUID(tx_id),
                document_type=tx.type,
                kind=KIND_POST_LEDGER,
                status=STATUS_BLOCKED,
                error_msgs=(joined[:1000] or None),
                result=result_payload,
                ran_at=now,
            ))

        db.commit()
        cid = str(company.id)

    print("✅ Seeded duplicate demo data")
    print(f"   company_id      : {cid}")
    print(f"   company name    : {COMPANY_NAME}")
    print(f"   documents       : {len(txns)}")
    print(f"   trapped rows    : {len(grouped)}")
    print(f"   duplicate flags : {len(flags)}")
    print()
    print("   View in frontend / API:")
    print(f"   GET /api/v1/health/trapped-invoices/?company_id={cid}")
    print(f"   GET /api/v1/health/stats/?company_id={cid}")


if __name__ == "__main__":
    main()
