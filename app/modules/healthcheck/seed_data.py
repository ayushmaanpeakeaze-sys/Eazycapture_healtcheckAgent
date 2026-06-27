"""Idempotent demo-data seed for the healthcheck POC.

Run with:

    python -m app.modules.healthcheck.seed_data
    # or  make seed

Inserts:

* Two companies (Demo Co + Test Co) with pinned UUIDs.
* ~30 invoices in Demo Co that collectively exercise every rule the
  AI service flags (Hamilton Smith duplicates, Net Connect drift,
  City Limousines tax outlier, future-dated bills, missing invoice
  numbers, capital-item review, paid-but-partial).
* ~5 invoices in Test Co with different vendor names to prove tenant
  isolation.
* 1–2 realistic line items per invoice.

Idempotency: every row is upserted with a fixed UUID. Running twice
leaves the table unchanged.
"""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.modules.healthcheck.models import Company, Invoice, InvoiceLineItem

# ---------------------------------------------------------------------
# Pinned UUIDs — keep in sync with the frontend's hardcoded demo IDs.
# ---------------------------------------------------------------------

DEMO_CO_ID = UUID("1a55c9dc-c48d-4ef6-a828-29d0298ebebd")
DEMO_CO_XERO_TENANT_ID = "f3a3aa51-33d9-4287-83d3-94d9988c82e4"

TEST_CO_ID = UUID("2b66dabc-d59e-5fed-b939-30e13a9fcfca")

# Hamilton Smith duplicate pair — same vendor + amount + close dates.
INV_HAMILTON_0001 = UUID("cb5119d0-9759-49d3-800b-7d0a90818178")
INV_HAMILTON_0005 = UUID("b7e0c5f4-9f52-4126-b102-45fd12eaa3ca")

# Net Connect drift — 1 outlier (account=453, tax=TAX001) + 4 normal.
INV_NET_CONNECT_OUTLIER = UUID("3f4a783e-6e8a-4101-9c08-aa11bb22cc33")
INV_NET_CONNECT_NORMAL_1 = UUID("4f4a783e-6e8a-4101-9c08-aa11bb22cc34")
INV_NET_CONNECT_NORMAL_2 = UUID("5f4a783e-6e8a-4101-9c08-aa11bb22cc35")
INV_NET_CONNECT_NORMAL_3 = UUID("6f4a783e-6e8a-4101-9c08-aa11bb22cc36")
INV_NET_CONNECT_NORMAL_4 = UUID("7f4a783e-6e8a-4101-9c08-aa11bb22cc37")

# City Limousines — 1 outlier (TAX001) + 3 normal (OUTPUT).
INV_CITY_LIMOS_OUTLIER = UUID("8c2d3e4f-1234-5678-9abc-def012345601")
INV_CITY_LIMOS_NORMAL_1 = UUID("8c2d3e4f-1234-5678-9abc-def012345602")
INV_CITY_LIMOS_NORMAL_2 = UUID("8c2d3e4f-1234-5678-9abc-def012345603")
INV_CITY_LIMOS_NORMAL_3 = UUID("8c2d3e4f-1234-5678-9abc-def012345604")

# Future-dated bills.
INV_FUTURE_1 = UUID("a8beb072-5464-46ab-b437-cce25e6f2a0a")
INV_FUTURE_2 = UUID("543f7cd8-323d-4baf-96b2-cb76831bccfb")
INV_FUTURE_3 = UUID("57f74b0c-137d-4627-8c08-388f49a62510")
INV_FUTURE_4 = UUID("2a35b64d-8564-43c8-a43f-e56b3f8dddcd")
INV_FUTURE_5 = UUID("1a29fe51-c150-4a4f-9f1f-cabad8a9c001")

# Missing invoice number.
INV_GATEWAY_MOTORS_NO_NUM = UUID("361a4980-7f53-4b68-820c-f6487f613284")
INV_MISC_NO_NUM_1 = UUID("f175784c-3ae0-4850-8504-46ad807181f7")
INV_MISC_NO_NUM_2 = UUID("a1234567-89ab-cdef-0123-456789abcde1")
INV_MISC_NO_NUM_3 = UUID("a1234567-89ab-cdef-0123-456789abcde2")

# Capital-item review (R&M £1063 in account 473).
INV_CAPITAL_REVIEW = UUID("14aa8bd8-7473-42c7-b8bc-ca83d7a75236")

# PAID-but-partial — genuine accounting error.
INV_PAID_PARTIAL = UUID("deadbeef-1234-5678-9abc-def012345678")

# Test Co — pinned so cross-tenant tests can assert specific ids.
TEST_CO_INV_1 = UUID("c0000001-0000-0000-0000-000000000001")
TEST_CO_INV_2 = UUID("c0000001-0000-0000-0000-000000000002")
TEST_CO_INV_3 = UUID("c0000001-0000-0000-0000-000000000003")
TEST_CO_INV_4 = UUID("c0000001-0000-0000-0000-000000000004")
TEST_CO_INV_5 = UUID("c0000001-0000-0000-0000-000000000005")


# ---------------------------------------------------------------------
# Invoice fixtures — each tuple is one (Invoice kwargs, [LineItem kwargs])
# ---------------------------------------------------------------------

def _line(
    description: str,
    quantity: str = "1",
    unit_amount: Optional[str] = None,
    account_code: Optional[str] = None,
    tax_type: Optional[str] = None,
    line_amount: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "description": description,
        "quantity": Decimal(quantity),
        "unit_amount": Decimal(unit_amount) if unit_amount else None,
        "account_code": account_code,
        "tax_type": tax_type,
        "line_amount": Decimal(line_amount) if line_amount else None,
    }


def _demo_invoices() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    out: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    # --- Hamilton Smith duplicate pair (ACCREC, both PAID) ---
    for inv_id, number, issue in (
        (INV_HAMILTON_0001, "INV-0001", date(2026, 3, 21)),
        (INV_HAMILTON_0005, "INV-0005", date(2026, 3, 22)),
    ):
        out.append((
            dict(
                id=inv_id,
                invoice_number=number,
                vendor_name="Hamilton Smith Ltd",
                amount=Decimal("541.25"),
                amount_paid=Decimal("541.25"),
                amount_due=Decimal("0.00"),
                issue_date=issue,
                due_date=date(2026, 4, 21),
                status="PAID",
                type="ACCREC",
                tax_code="OUTPUT",
                account_code="200",
                reference="Consulting",
                currency_code="GBP",
            ),
            [_line("Consulting services, March 2026",
                   unit_amount="541.25", line_amount="541.25",
                   account_code="200", tax_type="OUTPUT")],
        ))

    # --- Net Connect drift: 1 outlier + 4 normal ---
    out.append((
        dict(
            id=INV_NET_CONNECT_OUTLIER,
            invoice_number="NC-2024",
            vendor_name="Net Connect",
            amount=Decimal("132.00"),
            amount_paid=Decimal("0.00"),
            amount_due=Decimal("132.00"),
            issue_date=date(2026, 3, 1),
            due_date=date(2026, 3, 31),
            status="AUTHORISED",
            type="ACCPAY",
            tax_code="TAX001",        # outlier tax code
            account_code="453",       # outlier account
        ),
        [_line("Broadband — March", unit_amount="132.00",
               line_amount="132.00", account_code="453", tax_type="TAX001")],
    ))
    for i, (inv_id, num, issue) in enumerate((
        (INV_NET_CONNECT_NORMAL_1, "NC-2020", date(2025, 11, 1)),
        (INV_NET_CONNECT_NORMAL_2, "NC-2021", date(2025, 12, 1)),
        (INV_NET_CONNECT_NORMAL_3, "NC-2022", date(2026, 1, 1)),
        (INV_NET_CONNECT_NORMAL_4, "NC-2023", date(2026, 2, 1)),
    )):
        out.append((
            dict(
                id=inv_id,
                invoice_number=num,
                vendor_name="Net Connect",
                amount=Decimal("132.00"),
                amount_paid=Decimal("132.00"),
                amount_due=Decimal("0.00"),
                issue_date=issue,
                due_date=issue,
                status="PAID",
                type="ACCPAY",
                tax_code="INPUT",
                account_code="489",   # the dominant account
            ),
            [_line(f"Broadband — month {i + 1}",
                   unit_amount="132.00", line_amount="132.00",
                   account_code="489", tax_type="INPUT")],
        ))

    # --- City Limousines: 1 outlier (TAX001) + 3 normal (OUTPUT) ---
    out.append((
        dict(
            id=INV_CITY_LIMOS_OUTLIER,
            invoice_number="CL-1099",
            vendor_name="City Limousines",
            amount=Decimal("21.70"),
            amount_paid=Decimal("0.00"),
            amount_due=Decimal("21.70"),
            issue_date=date(2026, 4, 9),
            due_date=date(2026, 5, 9),
            status="AUTHORISED",
            type="ACCREC",
            tax_code="TAX001",       # outlier
            account_code="200",
        ),
        [_line("Airport transfer", unit_amount="21.70",
               line_amount="21.70", account_code="200", tax_type="TAX001")],
    ))
    for i, (inv_id, num, issue, amt) in enumerate((
        (INV_CITY_LIMOS_NORMAL_1, "CL-1095", date(2026, 3, 12), "30.00"),
        (INV_CITY_LIMOS_NORMAL_2, "CL-1096", date(2026, 3, 19), "25.00"),
        (INV_CITY_LIMOS_NORMAL_3, "CL-1097", date(2026, 4, 2), "42.00"),
    )):
        out.append((
            dict(
                id=inv_id,
                invoice_number=num,
                vendor_name="City Limousines",
                amount=Decimal(amt),
                amount_paid=Decimal(amt),
                amount_due=Decimal("0.00"),
                issue_date=issue,
                due_date=issue,
                status="PAID",
                type="ACCREC",
                tax_code="OUTPUT",
                account_code="200",
            ),
            [_line("City transfer", unit_amount=amt, line_amount=amt,
                   account_code="200", tax_type="OUTPUT")],
        ))

    # --- Future-dated bills ---
    for inv_id, issue, amt in (
        (INV_FUTURE_1, date(2026, 6, 18), "45.00"),
        (INV_FUTURE_2, date(2026, 6, 21), "80.00"),
        (INV_FUTURE_3, date(2026, 7, 19), "42.00"),
        (INV_FUTURE_4, date(2026, 7, 22), "113.00"),
        (INV_FUTURE_5, date(2026, 7, 30), "56.00"),
    ):
        out.append((
            dict(
                id=inv_id,
                invoice_number=f"FUT-{str(inv_id)[:8]}",
                vendor_name="Future Vendor Co",
                amount=Decimal(amt),
                amount_paid=Decimal("0.00"),
                amount_due=Decimal(amt),
                issue_date=issue,
                due_date=issue,
                status="AUTHORISED",
                type="ACCPAY",
                tax_code="INPUT",
                account_code="461",
            ),
            [_line("Office supplies", unit_amount=amt, line_amount=amt,
                   account_code="461", tax_type="INPUT")],
        ))

    # --- Missing invoice_number ---
    for inv_id, vendor, amt, account, when in (
        (INV_GATEWAY_MOTORS_NO_NUM, "Gateway Motors", "411.35", "429", date(2026, 4, 18)),
        (INV_MISC_NO_NUM_1, "Paper Supplies Ltd", "29.50", "429", date(2026, 4, 20)),
        (INV_MISC_NO_NUM_2, "Cleaning Co", "55.00", "461", date(2026, 4, 22)),
        (INV_MISC_NO_NUM_3, "Misc Vendor", "120.00", "461", date(2026, 4, 25)),
    ):
        out.append((
            dict(
                id=inv_id,
                invoice_number=None,
                vendor_name=vendor,
                amount=Decimal(amt),
                amount_paid=Decimal("0.00"),
                amount_due=Decimal(amt),
                issue_date=when,
                due_date=when,
                status="AUTHORISED",
                type="ACCPAY",
                tax_code="INPUT",
                account_code=account,
            ),
            [_line(f"{vendor} supplies", unit_amount=amt, line_amount=amt,
                   account_code=account, tax_type="INPUT")],
        ))

    # --- Capital item review (R&M £1063 in account 473) ---
    out.append((
        dict(
            id=INV_CAPITAL_REVIEW,
            invoice_number="RM-7781",
            vendor_name="Plant & Machinery Co",
            amount=Decimal("1063.56"),
            amount_paid=Decimal("0.00"),
            amount_due=Decimal("1063.56"),
            issue_date=date(2026, 3, 12),
            due_date=date(2026, 4, 12),
            status="AUTHORISED",
            type="ACCPAY",
            tax_code="INPUT",
            account_code="473",   # Repairs & Maintenance — capital review trigger
            reference="Workshop refit",
        ),
        [_line("Workshop ventilation upgrade", unit_amount="1063.56",
               line_amount="1063.56", account_code="473", tax_type="INPUT")],
    ))

    # --- PAID-but-partial (genuine error) ---
    out.append((
        dict(
            id=INV_PAID_PARTIAL,
            invoice_number="OOPS-001",
            vendor_name="Accidentally Closed Ltd",
            amount=Decimal("500.00"),
            amount_paid=Decimal("100.00"),
            amount_due=Decimal("400.00"),
            issue_date=date(2026, 2, 14),
            due_date=date(2026, 3, 14),
            status="PAID",      # claims PAID but amount_due > 0
            type="ACCPAY",
            tax_code="INPUT",
            account_code="429",
        ),
        [_line("Partial-pay contract", unit_amount="500.00",
               line_amount="500.00", account_code="429", tax_type="INPUT")],
    ))

    return out


def _test_co_invoices() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Distinct vendor names + amounts so cross-tenant tests can spot
    Test Co rows leaking into Demo Co's queries."""
    rows = [
        (TEST_CO_INV_1, "TC-001", "Aurora Labs",     "210.00", "ACCREC", "OUTPUT", "200"),
        (TEST_CO_INV_2, "TC-002", "Bluebird Bakery", "85.50",  "ACCPAY", "INPUT",  "461"),
        (TEST_CO_INV_3, "TC-003", "Cresta Holdings", "999.99", "ACCPAY", "INPUT",  "489"),
        (TEST_CO_INV_4, "TC-004", "Delta Imports",   "1500.00","ACCREC", "OUTPUT", "200"),
        (TEST_CO_INV_5, "TC-005", "Echo Logistics",  "42.42",  "ACCPAY", "INPUT",  "429"),
    ]
    out = []
    for inv_id, num, vendor, amt, doctype, tax, account in rows:
        out.append((
            dict(
                id=inv_id,
                invoice_number=num,
                vendor_name=vendor,
                amount=Decimal(amt),
                amount_paid=Decimal("0.00"),
                amount_due=Decimal(amt),
                issue_date=date(2026, 4, 1),
                due_date=date(2026, 5, 1),
                status="AUTHORISED",
                type=doctype,
                tax_code=tax,
                account_code=account,
            ),
            [_line(f"{vendor} order #{num}", unit_amount=amt,
                   line_amount=amt, account_code=account, tax_type=tax)],
        ))
    return out


# ---------------------------------------------------------------------
# Upsert helpers — idempotent because we pin every primary key.
# ---------------------------------------------------------------------

async def _ensure_company(
    db: AsyncSession,
    *,
    company_id: UUID,
    name: str,
    xero_tenant_id: Optional[str],
) -> Company:
    existing = await db.get(Company, company_id)
    if existing is not None:
        return existing
    company = Company(
        id=company_id,
        name=name,
        xero_tenant_id=xero_tenant_id,
        is_active=True,
    )
    db.add(company)
    await db.flush()
    return company


async def _ensure_invoices(
    db: AsyncSession,
    company_id: UUID,
    rows: Iterable[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> tuple[int, int]:
    """Insert any invoice + line items whose id isn't already in the DB.
    Returns (inserted_count, skipped_count)."""
    inserted = 0
    skipped = 0
    for invoice_kwargs, line_kwargs in rows:
        invoice_id: UUID = invoice_kwargs["id"]
        existing = await db.execute(
            select(Invoice.id).where(Invoice.id == invoice_id)
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue
        invoice = Invoice(company_id=company_id, **invoice_kwargs)
        db.add(invoice)
        await db.flush()
        for line in line_kwargs:
            db.add(InvoiceLineItem(invoice_id=invoice.id, **line))
        inserted += 1
    return inserted, skipped


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

async def seed() -> None:
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await _ensure_company(
                db,
                company_id=DEMO_CO_ID,
                name="Demo Company (Global)",
                xero_tenant_id=DEMO_CO_XERO_TENANT_ID,
            )
            await _ensure_company(
                db,
                company_id=TEST_CO_ID,
                name="Test Co",
                xero_tenant_id=None,
            )
            demo_inserted, demo_skipped = await _ensure_invoices(
                db, DEMO_CO_ID, _demo_invoices(),
            )
            test_inserted, test_skipped = await _ensure_invoices(
                db, TEST_CO_ID, _test_co_invoices(),
            )
        print(
            f"seed complete — "
            f"Demo Co: +{demo_inserted} inserted, {demo_skipped} skipped; "
            f"Test Co: +{test_inserted} inserted, {test_skipped} skipped."
        )


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
