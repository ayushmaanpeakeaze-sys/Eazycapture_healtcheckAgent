# Check Spec — Old Purchase Credits

> **Same check as [Old Sales Credits](CHECK_OLD_SALES_CREDITS.md), supplier
> side.** Purchase credit notes old + **not attached to a bill or refunded**. One
> engine: ACCRECCREDIT → `old_unsettled_sales_credit`, ACCPAYCREDIT →
> `old_unsettled_purchase_credit`. Same features/settings/actions. This page
> lists only what differs.

---

## What it is (Xenon)
Flags **supplier credit notes** created a while ago, still **not attached to a
bill or refunded** → our money sitting with the supplier, balances distorted.

**Why unattached (5 reasons — purchase versions):** created in error · bank rec
behind (supplier refund not allocated) · refund allocated to wrong credit/supplier
· refund coded **directly to a nominal account** (not via creditors ledger) · left
on account because no bills raised yet.

## Xenon features & settings — identical to Old Sales Credits
View · **Void/Delete** (can't if part-allocated/refunded — unallocate first) ·
Dismiss · **Ignore (30 days)** · Show Dismissed (+ add-back) · Search · Bulk
(Void/Dismiss/Ignore-30). Setting: credit note ≥ **X days old** (default **60**,
from credit-note date).

## What differs from Old Sales Credits (only this)
| | Sales credit | Purchase credit |
|---|---|---|
| Xero type | ACCRECCREDIT | **ACCPAYCREDIT** |
| Our issue type | `old_unsettled_sales_credit` | **`old_unsettled_purchase_credit`** |
| Contact | customer | **supplier** |
| Meaning | we owe the customer | **supplier owes us** (our money on account) |
| Attach to | an invoice | **a bill** |

Everything else — credit-type only, outstanding net of allocated, age vs
threshold, HIGH severity — is the **same shared logic**.

## Status (same as Old Sales Credits)
- ✅ Detection built (~85%) — live: Swanston Security & PC Complete purchase credits, 64 days, outstanding > 0 → `old_unsettled_purchase_credit`.
- ❌ To build (shared): **separate credit age** (currently reuses invoice/bill 60-day), small-remainder cutoff, archived-contact write-off suggestion, actions (Void/Delete / Dismiss / Ignore-30 / bulk / search).

## Xenon comparison (one line)
Mirror of Old Sales Credits. Detection ~85%. Gaps = separate credit age,
small-remainder handling, and the action buttons — same as the sales side.
