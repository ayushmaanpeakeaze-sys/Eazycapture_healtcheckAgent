# Check Spec — Old Unpaid Invoices (+ Bills mirror)

Everything Xenon's "Old Unpaid Invoices" does — every feature, action,
threshold — and exactly what we have vs need. The engine handles both sides:
sales → `old_unpaid_invoice`, purchase → `old_unpaid_bill` (Old Unpaid Bills is
the mirror).

---

## What it is (Xenon)
Lists invoices created a while ago that are **still not marked paid** in Xero.
Old debtors distort the position and may mean money was missed.

**Why an invoice shows unpaid (Xenon lists 6 reasons):**
1. Invoice created in error.
2. Bank rec not up to date — payment not yet allocated.
3. Payment allocated to the wrong invoice/customer.
4. Payment coded **directly to a sales account** (not through the debtors ledger).
5. Discount/write-off agreed but **no credit note** raised against it.
6. Customer just hasn't paid yet.

---

## Xenon's features (every button)
| Feature | What it does |
|---|---|
| **View in Xero** | deep-link to the invoice in Xero |
| **Void** | remove a wrong invoice (write-back); *can't if payment/credit allocated — unallocate first* |
| **Create Credit Note** | raise a credit note against the invoice in Xero |
| **Dismiss Item** | hide one invoice as "ok to leave unpaid"; won't reappear |
| **Ignore (30 days)** | snooze — hide for 30 days; **reappears after 30 days** if still unpaid |
| **Show Dismissed Items** | toggle to see dismissed/ignored; "Add back to issue list" button |
| **Search Text Filter** | live filter the list (includes dismissed/ignored) |
| **Bulk Process** | checkboxes → Void / Dismiss / Ignore-30-days in bulk |

## Xenon's settings / threshold
| Setting | Default | Meaning |
|---|---|---|
| Invoice is at least **X days old** | **60 days** | min age to flag — measured from the **invoice date** |

---

## Our detection logic (what's actually built)
- Skip credit notes (separate check); keep only **open/approved** docs.
- **Outstanding** = `amount_due`, else `amount − paid − allocated_credit`. ≤ 0 → fully settled, skip.
- **Age = today − DUE date** (not invoice date) — terms-aware, so a 90-day-terms customer isn't wrongly flagged at 60. *(This is our improvement; confirm the default with sir.)*
- Age < threshold (**60 days**, `_OVERDUE_DAYS_THRESHOLD`) → skip.
- Emit `old_unpaid_invoice` (sale) / `old_unpaid_bill` (purchase); **severity rises with age**.

## Edge cases
- **FP — within agreed terms:** customer on 90-day terms shows "old" at 60 but is fine → age-from-due-date fixes most; treat as review.
- **FP — payment arrived but not allocated/reconciled:** money in, just not matched → not a real debt.
- **FP — write-off agreed, no credit note yet:** settled in spirit, shows unpaid.
- **FN — Bill-or-Direct:** a bill paid directly from bank (not via ledger) still shows "unpaid" → real issue is double-counting; cross-check with Bill-or-Direct.
- **FN — foreign currency:** evaluate outstanding in base currency or age/amount is wrong.

## Configurable settings (Xenon parity + our extras)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Min age | 60 days | `_OVERDUE_DAYS_THRESHOLD = 60` (const) | per-client |
| Age measured from | invoice date | **due date** (terms-aware) | keep, make toggle (due vs invoice) |
| Severity by age | (none) | ✅ rises with age | per-client curve |
| Ignore-for-30-days | ✅ | ❌ | add snooze (reappear after 30d) |

## Logic (pseudo)
```python
for tx in documents:
    if tx.type in CREDIT_TYPES: continue
    if tx.status not in OPEN_STATUSES: continue
    outstanding = tx.amount_due or (tx.amount - tx.paid - tx.allocated)
    if outstanding <= 0: continue                      # fully settled
    age = today - (tx.due_date or tx.date)             # due-date, terms-aware
    if age < OVERDUE_THRESHOLD: continue               # 60d
    emit(tx, old_unpaid_invoice if is_sale(tx) else old_unpaid_bill,
         severity=by_age(age), outstanding=outstanding)
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~90%):** open-only, outstanding net of credit, **due-date age**, 60-day threshold, severity by age. (Live: City Limousines INV-0006 £250, 71 days → flagged.)
- ❌ **To build (mostly actions + config):**
  1. **Per-client age** threshold + due-vs-invoice-date toggle.
  2. **Ignore-30-days** snooze (reappear after 30d).
  3. **Actions:** Void (write-back), **Create Credit Note** (write-back), Dismiss / Show-Dismissed (flags exist in repo; need toggle + add-back), **bulk** process, **search filter**.
  4. **Cross-check with Bill-or-Direct** to avoid the double-counting false-negative.

## Xenon comparison (one line)
Detection ~90% and arguably better (we age from **due date**, terms-aware;
severity scales with age). Gaps are **per-client age setting**, **ignore-30-days
snooze**, and the **action buttons** (Void / Create-Credit-Note / Dismiss / bulk
/ search). Old Unpaid **Bills** is the same check on the purchase side.
