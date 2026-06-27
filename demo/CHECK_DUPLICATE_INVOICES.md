# Check Spec — Duplicate Invoices / Bills

What Xenon's "Duplicate Customer Invoices" (and Supplier Bills) does, its
features/settings, and exactly what we have vs need to build.

---

## What it is (Xenon)
Flags invoices (and bills) that were likely **entered twice in error**. Same
document twice → reports overstated, customer/supplier may pay twice, relationship
strain. The page lists potential duplicate **matches** for the user to action.

## Xenon's features (what it does)
1. **View in Xero** — per match, deep-link to the invoice in Xero to investigate.
2. **Void** — remove a wrong invoice from Xero (write-back). *Caveat: can't void if a payment/credit note is allocated — must unallocate first.*
3. **Dismiss Match** — hide a specific pair as "not a duplicate"; it won't reappear.
4. **Show Dismissed Matches** — toggle to review/restore previously dismissed pairs.
5. **Bulk Process** — checkboxes + dropdown → dismiss many matches at once.
6. **Settings (4 toggles)** — see table below.

### Xenon's 4 settings
| Setting | Default | Meaning |
|---|---|---|
| Date within **X days** | **1 day** (same day) | max gap between the two invoice dates |
| Reference exactly same | **OFF** | only flag if the reference matches exactly |
| Value exactly same | **OFF** | only flag if the amount matches exactly |
| Also check paid invoices | **OFF** | OFF = at least one must be **unpaid** to flag |

---

## Our detection logic (what's actually built)
- **Group** by `contact_id` + `amount` + a date bucket (so we don't compare every pair).
- Within a group, same **type/direction only** (invoice↔invoice, bill↔bill; never invoice↔its credit note).
- **Reference tiers:** exact ref → conf **0.97**; normalised same (`INV-1` = `inv1`) → **0.95**; no reference → **0.85**; different reference → currently **dropped**.
- **Recurring exclusion:** same amount every ~month (subscription/rent) → not a duplicate.
- **Date window:** **7 days** (ours) — catches adjacent-day dupes while excluding monthly recurring.
- **Credit-aware outstanding:** nets allocated credit so a credit-settled invoice isn't mis-read.
- Earlier of the two dates = the "original".

## Edge cases
- **FP — recurring charge:** monthly £500 rent/subscription → excluded by recurring check.
- **FP — equal instalments / deposit + balance:** same amount, legit separate docs → different-reference case isn't flagged.
- **FP — invoice + its credit note:** same contact/amount → avoided by direction/type-aware compare.
- **FN — same bill, different reference** (`INV-88` vs `88/2024`): different ref → currently **dropped** (should become a LOW "review", not silently dropped).
- **FN — split bill** (£1,200 vs £600+£600) or **shipping/tax added** → amount differs → won't group.

---

## Configurable settings (Xenon parity + our gap)
We **match all 4 of Xenon's settings conceptually**, but ours are **hardcoded
constants today**, not per-client:
| Xenon setting | Ours now | To do |
|---|---|---|
| Date within X days | `_DUPLICATE_DAYS_WINDOW = 7` (const) | make per-client (default could be 1–7) |
| Reference exactly same | tiered (always uses ref) | add a "strict ref only" toggle |
| Value exactly same | always requires same amount | add a small **tolerance** option (our improvement) |
| Also check paid | currently includes paid | add "at least one unpaid" toggle (default ON) |

---

## Logic (pseudo)
```python
groups = group_by(active_docs, key=(contact_key, amount, date_bucket(date, WINDOW)))
for (a, b) in pairs(group) where same_direction and abs(a.date-b.date) <= WINDOW:
    if   raw_equal(a.ref, b.ref):     tier = 0.97
    elif norm(a.ref) == norm(b.ref):  tier = 0.95
    elif no_ref(a) or no_ref(b):      tier = 0.85
    else: continue                     # different ref → (todo: LOW review, not drop)
    if looks_recurring(contact, amount): continue
    outstanding = amount - paid - allocated_credit
    emit_pair(a, b, tier, original=earlier_date(a, b))
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~90%):** grouping, ref tiers, recurring exclusion, credit/direction-aware, 7-day window. (Hamilton Smith adjacent-day case verified live.)
- ❌ **To build:**
  1. **Per-client settings** (the 4 toggles above) via `audit_config`.
  2. **Different-reference → LOW "review"** instead of dropping (catches `INV-88` vs `88/2024`).
  3. **Value tolerance** option.
  4. **Actions:** Void (Xero write-back — careful with allocated payments/credits), Dismiss/Show-Dismissed (we have dismiss/resolve flags in the repo; need bulk + show-dismissed toggle), **bulk dismiss**.

## Xenon comparison (one line)
We match Xenon's 4 settings conceptually **and** add value-tolerance +
credit-note handling. Detection is solid; the gaps are **making settings
per-client** and **the action buttons** (Void / Dismiss / bulk), plus turning
the different-reference case into a review instead of a drop.
