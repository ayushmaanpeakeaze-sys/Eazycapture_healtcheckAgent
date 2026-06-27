# Check Spec — Duplicate Supplier Bills

> **Same check as [Duplicate Invoices](CHECK_DUPLICATE_INVOICES.md), purchase
> side.** One engine function (`_find_duplicate_bills`) handles both: a sales
> doc (ACCREC) becomes `duplicate_invoice`, a purchase doc (ACCPAY) becomes
> `duplicate_bill`. The detection logic, edge cases, settings, and build status
> are identical — see that doc for the full detail. This page lists only what
> differs for bills.

---

## What it is (Xenon)
Flags **supplier bills** entered twice in error. Impact differs from invoices:
- Invoices (sales): customer may **pay you twice**, reports overstated.
- **Bills (purchases): you may pay the supplier twice → cash-flow loss**, costs overstated.

## Xenon features — identical to invoices
View bill in Xero · **Void** (can't if a payment/credit note is allocated —
unallocate first) · Dismiss Match · Show Dismissed · Bulk dismiss.

## Xenon settings — identical 4 (defaults same)
| Setting | Default |
|---|---|
| Date of bills within **X days** | **1 day** |
| **Bill** reference exactly same | OFF |
| Total value exactly same | OFF |
| Also check **paid bills** | OFF (≥1 must be unpaid) |

## What differs from the invoices check (only this)
| | Invoices | Bills |
|---|---|---|
| Xero type | ACCREC | **ACCPAY** |
| Our issue type | `duplicate_invoice` | **`duplicate_bill`** |
| Reference field | customer invoice ref | **supplier's bill reference** |
| Business risk | customer pays twice | **you pay supplier twice (cash flow)** |
| "Contact" | customer | **supplier** |

Everything else — grouping (contact + amount + 7-day window), reference tiers
(0.97 / 0.95 / 0.85), recurring exclusion, credit-aware outstanding,
direction-aware (bill ↔ bill only, never bill ↔ its credit note) — is the **same
shared logic**.

## Status (same as invoices)
- ✅ Detection built (~90%) — verified: e.g. Globex `SUP-778` two bills, 2 days apart → `duplicate_bill`.
- ❌ To build (shared with invoices): per-client settings (the 4 toggles), different-reference → LOW "review" instead of drop, value tolerance, and the action buttons (Void / Dismiss / bulk / show-dismissed).

## Xenon comparison (one line)
Mirror of Duplicate Invoices. We match the 4 settings conceptually + add
value-tolerance and credit-note handling; gaps are per-client settings and the
action buttons (Void/Dismiss/bulk) — same as the invoices side.
