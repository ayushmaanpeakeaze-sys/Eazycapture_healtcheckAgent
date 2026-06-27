"""Demo snapshot loader — mirror live Xero data into queryable SQL tables.

WHY THIS EXISTS
---------------
The health-check engine runs its rules in Python over data fetched live from
Xero *in memory*; the application database stores only the FINDINGS. That makes
it impossible to show "the SQL query behind each check" — there's no raw table
to query.

This script fetches the SAME live data the audit sees (invoices, credit notes,
contacts, chart of accounts) and writes it into purpose-built ``snap_*`` tables,
so every health check can be expressed as a runnable SQL query that returns the
real flagged rows. See ``demo/rulebook.sql``.

Run:
    .venv/bin/python demo/snapshot.py

It is read-only against Xero and only writes the ``snap_*`` demo tables — it
never touches the real application tables.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text

from app.core.db import SyncSessionLocal, sync_engine
from app.modules.healthcheck.models import Company
from app.modules.healthcheck.tasks import _reshape_xero_to_batch, _map_xero_accounts
from app.modules.integrations.service import IntegrationService
from app.services.healthcheck.deterministic import _normalize_ref


# ---------------------------------------------------------------------------
# small parse helpers (Xero gives strings / mixed types)
# ---------------------------------------------------------------------------

def _to_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_num(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


# ---------------------------------------------------------------------------
# DDL — drop + recreate the snapshot tables
# ---------------------------------------------------------------------------

_DDL = """
DROP TABLE IF EXISTS snap_invoice_lines;
DROP TABLE IF EXISTS snap_invoices;
DROP TABLE IF EXISTS snap_contacts;
DROP TABLE IF EXISTS snap_accounts;

CREATE TABLE snap_invoices (
    transaction_id        text PRIMARY KEY,
    contact_id            text,
    vendor_name           text,
    type                  text,      -- ACCREC | ACCPAY | ACCRECCREDIT | ACCPAYCREDIT
    status                text,      -- AUTHORISED | PAID | DRAFT | SUBMITTED ...
    reference             text,      -- supplier's invoice number (raw)
    normalized_reference  text,      -- our _normalize_ref(reference)
    invoice_number        text,      -- Xero's own number
    amount                numeric(14,2),
    amount_paid           numeric(14,2),
    amount_due            numeric(14,2),
    date                  date,
    due_date              date,
    currency_code         text,
    account_code          text,      -- first line's account code
    tax_code              text,      -- first line's tax code
    description           text       -- first line / reference description
);

CREATE TABLE snap_invoice_lines (
    transaction_id  text,
    line_no         int,
    account_code    text,
    tax_code        text,
    amount          numeric(14,2),
    description     text
);

CREATE TABLE snap_contacts (
    contact_id              text PRIMARY KEY,
    name                    text,
    is_supplier             boolean,
    is_customer             boolean,
    is_archived             boolean,
    email                   text,
    tax_number              text,
    bank_account            text,
    phone                   text,
    purchases_default_code  text,
    sales_default_code      text
);

CREATE TABLE snap_accounts (
    code       text PRIMARY KEY,
    name       text,
    type       text,        -- FIXED | EXPENSE | REVENUE | CURRENT ...
    statement  text         -- 'Balance Sheet' | 'P&L'
);
"""


async def _fetch_live(company: Company):
    integ = IntegrationService()
    cid, tid = company.nango_connection_id, company.xero_tenant_id
    invoices, credit_notes, contacts, accounts = await asyncio.gather(
        integ.fetch_all_invoices(cid, tid),
        integ.fetch_all_credit_notes(cid, tid),
        integ.fetch_contacts(cid, tid),
        integ.fetch_chart_of_accounts(cid, tid),
    )
    return invoices + credit_notes, contacts, accounts


def main():
    with SyncSessionLocal() as db:
        company = db.execute(
            select(Company).where(
                Company.nango_connection_id.isnot(None),
                Company.xero_tenant_id.isnot(None),
            )
        ).scalars().first()
        if not company:
            raise SystemExit("No Xero-connected company found — connect one first.")
        print(f"Company: {company.name}  (id={company.id})")

    raw_docs, contacts, accounts_raw = asyncio.run(_fetch_live(company))
    shaped = [s for s in (_reshape_xero_to_batch(d) for d in raw_docs) if s]
    accounts = _map_xero_accounts(accounts_raw)
    print(f"Fetched: {len(shaped)} documents, {len(contacts)} contacts, "
          f"{len(accounts)} accounts")

    with sync_engine.begin() as conn:
        # DDL (split: asyncpg/psycopg can't always run multiple stmts at once)
        for stmt in filter(str.strip, _DDL.split(";")):
            conn.execute(text(stmt))

        for s in shaped:
            conn.execute(text("""
                INSERT INTO snap_invoices (transaction_id, contact_id, vendor_name,
                    type, status, reference, normalized_reference, invoice_number,
                    amount, amount_paid, amount_due, date, due_date, currency_code,
                    account_code, tax_code, description)
                VALUES (:tid, :cid, :vendor, :type, :status, :ref, :nref, :invno,
                    :amount, :paid, :due, :date, :duedate, :ccy, :acct, :tax, :desc)
                ON CONFLICT (transaction_id) DO NOTHING
            """), {
                "tid": s["transaction_id"], "cid": s.get("contact_id"),
                "vendor": s.get("vendor_name"), "type": s.get("type"),
                "status": s.get("status"), "ref": s.get("reference"),
                "nref": _normalize_ref(s.get("reference")),
                "invno": s.get("invoice_number"),
                "amount": _to_num(s.get("amount")),
                "paid": _to_num(s.get("amount_paid")),
                "due": _to_num(s.get("amount_due")),
                "date": _to_date(s.get("date")),
                "duedate": _to_date(s.get("due_date")),
                "ccy": s.get("currency_code"),
                "acct": s.get("current_account_code"),
                "tax": s.get("tax_code"),
                "desc": s.get("description"),
            })
            for i, li in enumerate(s.get("line_items") or []):
                conn.execute(text("""
                    INSERT INTO snap_invoice_lines (transaction_id, line_no,
                        account_code, tax_code, amount, description)
                    VALUES (:tid, :ln, :acct, :tax, :amt, :desc)
                """), {
                    "tid": s["transaction_id"], "ln": i,
                    "acct": li.get("account_code"), "tax": li.get("tax_code"),
                    "amt": _to_num(li.get("amount")), "desc": li.get("description"),
                })

        for c in contacts:
            phone = ""
            for p in (c.get("Phones") or []):
                d = _digits(p.get("PhoneNumber"))
                if len(d) >= 6:
                    phone = d
                    break
            conn.execute(text("""
                INSERT INTO snap_contacts (contact_id, name, is_supplier, is_customer,
                    is_archived, email, tax_number, bank_account, phone,
                    purchases_default_code, sales_default_code)
                VALUES (:cid, :name, :sup, :cust, :arch, :email, :tax, :bank, :phone,
                    :pdc, :sdc)
                ON CONFLICT (contact_id) DO NOTHING
            """), {
                "cid": (c.get("ContactID") or "").strip(),
                "name": (c.get("Name") or "").strip(),
                "sup": _to_bool(c.get("IsSupplier")),
                "cust": _to_bool(c.get("IsCustomer")),
                "arch": _to_bool(c.get("IsArchived")),
                "email": (c.get("EmailAddress") or "").strip() or None,
                "tax": (c.get("TaxNumber") or "").strip() or None,
                "bank": (c.get("BankAccountDetails") or "").strip() or None,
                "phone": phone or None,
                "pdc": (c.get("PurchasesDefaultAccountCode") or "").strip() or None,
                "sdc": (c.get("SalesDefaultAccountCode") or "").strip() or None,
            })

        for a in accounts:
            conn.execute(text("""
                INSERT INTO snap_accounts (code, name, type, statement)
                VALUES (:code, :name, :type, :stmt)
                ON CONFLICT (code) DO NOTHING
            """), {
                "code": a["code"], "name": a["name"],
                "type": a["type"], "stmt": a["statement"],
            })

    print("\nSnapshot loaded into snap_invoices / snap_invoice_lines / "
          "snap_contacts / snap_accounts.")
    print("Now run:  psql \"postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc\" "
          "-f demo/rulebook.sql")


if __name__ == "__main__":
    main()
