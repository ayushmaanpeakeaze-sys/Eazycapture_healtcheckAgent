# Check Spec — Old Unpaid Bills

> **Same check as [Old Unpaid Invoices](CHECK_OLD_UNPAID_INVOICES.md), purchase
> side.** One engine path handles both: sale → `old_unpaid_invoice`, purchase →
> `old_unpaid_bill`. Detection, features, actions, threshold are identical — see
> that doc for full detail. This page lists only what differs for bills.

---

## What it is (Xenon)
Lists supplier **bills** added a while ago but still **not marked paid**.
Old creditors distort the position; sometimes the bill was already paid directly
and never matched.

**Why a bill shows unpaid (Xenon's 6 reasons — purchase versions):**
1. Bill created in error.
2. Bank rec behind — supplier payment not yet allocated.
3. Payment allocated to the wrong bill/supplier.
4. Payment coded **directly to an expense account** (not through the creditors ledger). ← Bill-or-Direct
5. Discount/write-off agreed but **no credit note** raised.
6. Just haven't paid the supplier yet.

## Xenon features & threshold — identical to invoices
View · **Void** (unallocate first if payment/credit attached) · **Create Credit
Note** (supplier) · Dismiss · **Ignore (30 days)** · Show Dismissed (+ add-back)
· Search filter · Bulk (Void / Dismiss / Ignore-30).
Setting: bill ≥ **X days old** (default **60**, from bill date).

## What differs from the invoices check (only this)
| | Invoices | Bills |
|---|---|---|
| Xero type | ACCREC | **ACCPAY** |
| Our issue type | `old_unpaid_invoice` | **`old_unpaid_bill`** |
| Contact | customer (debtor) | **supplier (creditor)** |
| Risk | debtors overstated | **creditors overstated / may pay twice** |
| Direct-payment reason | sales nominal account | **expense nominal account** |
| Credit note button | customer credit | **supplier credit** |

Everything else — open-only filter, outstanding net of allocated credit,
**age from DUE date** (terms-aware, our improvement), 60-day threshold, severity
by age — is the **same shared logic**.

## Status (same as invoices)
- ✅ Detection built (~90%) — live: Central Copiers bill £163.56 outstanding, 67 days → `old_unpaid_bill`.
- ❌ To build (shared): per-client age setting, Ignore-30-days snooze, action buttons (Void / Create-Credit-Note / Dismiss / bulk / search), Bill-or-Direct cross-check.

## Xenon comparison (one line)
Mirror of Old Unpaid Invoices. Detection ~90% (and we age from due date,
terms-aware). Gaps = per-client setting, ignore-30 snooze, and the action
buttons — same as the invoices side.
