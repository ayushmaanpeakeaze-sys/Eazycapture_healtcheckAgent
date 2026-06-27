# Check Spec — Unapproved Invoices (+ Bills mirror)

What Xenon's "Unapproved Invoices" does, and our version. Engine handles both:
sale → `unapproved_invoice`, purchase → `unapproved_bill` (Unapproved Bills is
the mirror).

---

## What it is (Xenon)
Flags invoices stuck in **Draft** or **Submitted** status → they **don't appear
in Xero's accounting reports**, so the real position is understated and other
checks miss them too.

**Why an invoice stays unapproved (4 reasons):**
1. Not reviewed yet.
2. Created in error.
3. Product/service not supplied yet.
4. Cancelled — invoice not required.

## Xenon's features
| Feature | What it does |
|---|---|
| **View/Edit in Xero** | deep-link to the invoice |
| **Approve** | approve the draft/submitted invoice in Xero (write-back); *button hidden if required data is missing* |
| **Delete** | remove an invoice created in error (write-back) |
| **Dismiss** | hide one that's ok to leave as draft |
| **Ignore (30 days)** | snooze — reappears after 30 days if still unapproved |
| **Show Dismissed** | toggle + "Add back to issue list" |
| **Search filter** | live filter (incl. dismissed/ignored) |
| **Bulk** | Approve / Dismiss / Delete / Ignore-30 in bulk |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Invoice ≥ **X days old** | **0 days** (immediate) | min age to flag — from the **invoice/create date** |

---

## Our logic (what's actually built)
- Status **DRAFT or SUBMITTED** → candidate.
- **Age = today − last-touch (updated) date** > **7 days** (`_UNAPPROVED_AGE_DAYS`).
- Emit `unapproved_invoice` (sale) / `unapproved_bill` (purchase); severity by age.

**Built today ≈ ~90%.** Key difference: **Xenon flags at 0 days (immediately)**;
we use a **7-day grace** (so month-end fresh drafts don't nag) and measure from
**last-touch** date (not create date) so a just-edited draft isn't "stale".
→ confirm this default with sir.

## Edge cases
- **FP — recurring template draft / fresh draft:** auto-created monthly or just-made → grace + last-touch date avoids nagging.
- **FP — month-end intentional drafts:** made deliberately to approve next period.
- **FN — future-dated draft** (advance invoice): could mislead → also look at due date.
- **FN — submitted-but-old:** if you only look at DRAFT you miss SUBMITTED → we include both.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Min age | 0 days | `_UNAPPROVED_AGE_DAYS = 7` (const) | per-client (default 7 vs 0 — confirm with sir) |
| Age from | invoice/create date | **last-touch (updated)** date | keep; make toggle |

## Logic (pseudo)
```python
for tx in documents:
    if tx.status not in (DRAFT, SUBMITTED): continue
    age = today - (tx.posted_date or tx.date)      # last-touch
    if age <= UNAPPROVED_GRACE_DAYS: continue       # fresh draft → skip
    emit(tx, unapproved_invoice if is_sale(tx) else unapproved_bill, by_age(age))
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~90%):** DRAFT/SUBMITTED, 7-day grace, last-touch age, severity. (Live: Rex Media Group DRAFT £550, 20 days → flagged.)
- ❌ **To build:**
  1. **Per-client age** threshold (+ confirm 7 vs Xenon's 0).
  2. **Ignore-30-days** snooze.
  3. **Actions:** Approve (write-back), Delete (write-back), Dismiss / Show-Dismissed, **bulk**, search filter.

## Xenon comparison (one line)
Detection ~90% (we add a 7-day grace + last-touch date so fresh drafts don't
nag). Gaps = per-client age, ignore-30 snooze, and the action buttons (Approve /
Delete / Dismiss / bulk). **Unapproved Bills** is the same check on the purchase
side (`unapproved_bill`).
