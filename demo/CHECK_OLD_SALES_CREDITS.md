# Check Spec — Old Sales Credits (+ Purchase Credits mirror)

What Xenon's "Old Sales Credits" does, and our version. Engine handles both:
ACCRECCREDIT → `old_unsettled_sales_credit`, ACCPAYCREDIT →
`old_unsettled_purchase_credit` (Old Purchase Credits is the mirror).

---

## What it is (Xenon)
Flags **sales credit notes** created a while ago that are **still not attached to
an invoice or refunded** → money left "on account", balances distorted.

**Why a credit note sits unattached (5 reasons):**
1. Created in error.
2. Bank rec behind — the refund not yet allocated.
3. Refund allocated to the wrong credit note/customer.
4. Refund coded **directly to a nominal account** (not via debtors ledger).
5. Left on account because no invoices raised for the customer yet.

## Xenon's features
| Feature | What it does |
|---|---|
| **View in Xero/QBO** | deep-link to the credit note |
| **Void / Delete** | remove a credit note created in error; *can't if part-allocated/part-refunded — unallocate first* |
| **Dismiss** | hide one that's ok to leave on account |
| **Ignore (30 days)** | snooze — reappears after 30 days if still unallocated |
| **Show Dismissed** | toggle + "Add back to issue list" |
| **Search filter** | live filter (incl. dismissed/ignored) |
| **Bulk** | Void / Dismiss / Ignore-30 |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Credit note ≥ **X days old** | **60 days** | min age, from the **credit note date** |

---

## Our logic (what's actually built)
- Only **credit-note** types. **Outstanding** = `amount_due`, else `amount − paid − allocated`. ≤ 0 → fully applied, skip.
- **Age = today − credit-note date** > threshold.
- Emit `old_unsettled_sales_credit` (ACCRECCREDIT) / `old_unsettled_purchase_credit` (ACCPAYCREDIT), severity HIGH.

**Built today ≈ ~85%.** Gap: uses the **shared 60-day invoice/bill threshold**,
not a **separate credit age**; no small-remainder cutoff.

## Edge cases
- **FP — intentionally open:** kept open because the next invoice (to adjust against) is coming → not yet an error.
- **FP — tiny remainder** (£2–£5 left): showing HIGH is noise → small-remainder cutoff → LOW.
- **FP — refund in process:** sent but not reconciled yet → settling.
- **FN — archived/dead contact:** credit will never be used → should pakdo + suggest write-off, else just shows "old".

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Credit age | 60 days | **shared** `_OVERDUE_DAYS_THRESHOLD` | **separate** credit age, per-client |
| Small-remainder cutoff | (none) | ❌ | tiny value → LOW severity |

## Logic (pseudo)
```python
for tx in documents where tx.type in CREDIT_TYPES:
    outstanding = tx.amount_due or (tx.amount - tx.paid - tx.allocated)
    if outstanding <= 0: continue
    if today - tx.date <= CREDIT_AGE_THRESHOLD: continue
    emit(tx, sales_credit if tx.type == ACCRECCREDIT else purchase_credit, HIGH)
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~85%):** credit-type, outstanding net of allocated, age, HIGH.
- ❌ **To build:**
  1. **Separate credit age** setting (currently reuses invoice/bill 60-day).
  2. **Small-remainder** cutoff (LOW).
  3. **Archived-contact → write-off suggestion**.
  4. **Actions:** Void/Delete, Dismiss / Show-Dismissed, Ignore-30, bulk, search.

## Xenon comparison (one line)
Detection ~85% (same intent — catch old unapplied credit). Gaps = a **separate
credit age** setting, small-remainder handling, and the action buttons. **Old
Purchase Credits** is the same check on the supplier side
(`old_unsettled_purchase_credit`).
