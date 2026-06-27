# Check Spec — Unapproved Bills

> **Same check as [Unapproved Invoices](CHECK_UNAPPROVED_INVOICES.md), purchase
> side.** Supplier bills stuck in **Draft/Submitted** → not in Xero reports. One
> engine path: sale → `unapproved_invoice`, purchase → `unapproved_bill`. Same
> features/settings/actions. This page lists only what differs.

---

## What it is (Xenon)
Flags supplier **bills** in **Draft** or **Submitted** status (not in accounting
reports → costs/creditors understated).

**Why a bill stays unapproved (Xenon's 6 reasons):** not reviewed · **in dispute**
· created in error · **added to Xero more than once** · not yet provided by
supplier · cancelled.

## Xenon features & settings — identical to invoices
View/Edit · **Approve** (write-back; hidden if data missing) · **Delete** ·
Dismiss · **Ignore (30 days)** · Show Dismissed (+ add-back) · Search · Bulk
(Approve/Dismiss/Delete/Ignore-30). Setting: bill ≥ **X days old** (default **0**,
from bill date).

## What differs from the invoices check (only this)
| | Invoices | Bills |
|---|---|---|
| Xero type | ACCREC | **ACCPAY** |
| Our issue type | `unapproved_invoice` | **`unapproved_bill`** |
| Contact | customer | **supplier** |
| Impact | income/debtors understated | **costs/creditors understated** |
| Extra reasons | — | **in dispute, entered twice** |

Everything else — DRAFT/SUBMITTED filter, **7-day grace**, **last-touch** age,
severity by age — is the **same shared logic**.

## Status (same as invoices)
- ✅ Detection built (~90%) — DRAFT/SUBMITTED + 7-day grace + last-touch age.
- ❌ To build (shared): per-client age (confirm 7 vs Xenon's 0), Ignore-30 snooze, actions (Approve / Delete / Dismiss / bulk / search).

## Xenon comparison (one line)
Mirror of Unapproved Invoices. Detection ~90% (we add a 7-day grace so fresh
drafts don't nag). Gaps = per-client age + the action buttons — same as invoices.
