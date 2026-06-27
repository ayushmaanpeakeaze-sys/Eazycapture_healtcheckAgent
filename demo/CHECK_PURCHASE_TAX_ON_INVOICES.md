# Check Spec — Purchase Tax on Invoices

> **Mirror of [Sales Tax on Bills](CHECK_SALES_TAX_ON_BILLS.md), sales side.** A
> **purchase/input tax code on a customer invoice** (e.g. "20% (VAT on Expenses)",
> "Exempt Expenses"). Same engine + flag-based classification. This page lists
> only what differs.

---

## What it is (Xenon)
Flags **customer invoices** posted with a **purchase tax code** → VAT return goes
wrong (output becomes input).

**Checked:** customer invoice lines. **OPTIONAL: Money In** — **OFF by default**
(deposits legitimately use a purchase tax code for purchase **refunds**) → a "Show
Bank Deposits Too" toggle appears.

## Features & settings — identical to Sales Tax on Bills
Details (Type · Contact · Date · Invoice ref · Account · Description · Net Amount ·
**Tax code**) · View/Edit · Show Bank Deposits Too toggle · Dismiss / Show-Dismissed
· Bulk.

## What differs from Sales Tax on Bills (only this)
| | Sales Tax on Bills | Purchase Tax on Invoices |
|---|---|---|
| Document | supplier **bill** (ACCPAY) | customer **invoice** (ACCREC) |
| Wrong code | sales/**output** tax on a purchase | purchase/**input** tax on a sale |
| Xero flag used | `CanApplyToExpenses = false` | **`CanApplyToRevenue = false`** |
| Our issue type | `sales_tax_on_bills` | **`purchase_tax_on_invoices`** |
| Optional bank scan | Money Out (off) | **Money In (off)** |

Everything else — all-line scan, flag-based (not name-based), reverse-charge
whitelist (to-do), same-account summary flag (to-do) — is the **same shared logic**.

## Status (same as Sales Tax on Bills)
- ✅ Detection built (~85%) — ACCREC line with `CanApplyToRevenue=false` → flagged.
- ❌ To build (shared): reverse-charge whitelist, optional **Money In** scan + toggle, same-account summary flag, View-Edit / Dismiss / bulk actions.

## Xenon comparison (one line)
Mirror of Sales Tax on Bills on the sales side. Flag-based classification is our
strength; gaps = whitelist + optional Money-In scan + actions.
