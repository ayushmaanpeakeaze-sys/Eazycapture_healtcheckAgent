# Check Spec — Bank Balance Check

What Xenon's "Bank Balance Check" does, and our version. **Status: not built as a
rule yet (❌), but easy** — the two balances it needs are both available from
Xero's API (unlike the raw feed). It's the cleanest bank check to build.

---

## What it is (Xenon)
Per bank account, flags where the **Bank Statement balance in Xero ≠ the Xero
Trial Balance value** for that account. A gap = something missing or
double-counted (bank work unfinished). Optionally the user enters the **physical
bank statement balance** (to confirm the feed pulled in correctly). Quantity/age
of unreconciled items feed the health score.

## Xenon's features
| Feature | What it does |
|---|---|
| **Per-account gap** | one row per account where statement balance ≠ trial balance; 0-gap accounts hidden (Show-all toggle) |
| **Physical balance input** | user types the real statement balance to compare |
| **Process** | deep-link to the Xero reconcile screen |
| **Upload supporting docs** | attach a statement/spreadsheet per period-end |
| **Add Note (+ tag)** | note per bank account / period-end |
| **Ignore / Reinstate account** | exclude e.g. a personal account |
| **Mark as OK** | accept a legit difference for the selected period-end (note recommended) |

## Xenon's settings
- **Exclude bank accounts** (per-client). · (Physical balance entry per period.)

---

## Why this one is easy for us (data available)
- **Trial Balance** — already fetched (`fetch_trial_balance`) → GL balance per account.
- **Statement/bank balance** — from `Reports/BankSummary` (closing balance) or the
  bank account balance — fetchable via API.
- So: `gap = statement_balance(account) − trial_balance(account)`. No raw feed
  needed → unlike Unprocessed/Unreconciled, **this is not Xero-limited.**

## Our logic (target)
```python
for account in bank_accounts:
    diff = xero_bank_statement_balance(account) - trial_balance(account)
    if abs(diff) > TOL:                       # small tolerance (rounding)
        emit(account, balance_difference, diff,
             cause={unreconciled_count(account)})   # our extra: show root cause
    # if user entered a physical balance, compare that too
```

## Edge cases
- **FP — cheque issued not cleared / timing:** temporary gap → tolerance + review.
- **FP — credit card / petty cash:** negative/rarely-reconciled → per-account low severity or skip.
- **FN — foreign-currency account:** compare in base currency or the gap is wrong.

## Configurable settings (target)
| Setting | Xenon | Target for us |
|---|---|---|
| Tolerance | (rounding) | per-client small tolerance |
| Per-account ignore / Mark-OK | ✅ | exclude list + per-period Mark-OK |
| Physical balance input | ✅ | optional user input |

---

## Status (what we have vs to build)
- ❌ **Not built as a rule.** But we already fetch **Trial Balance**; only need
  **BankSummary** (statement balance) + the per-account compare.
- ✅ **To build (easy):**
  1. Fetch **BankSummary** (closing/statement balance per account).
  2. Per-account `statement − trial-balance` gap > tolerance → flag.
  3. **Our extra:** show the gap's root cause (that account's unreconciled count).
  4. Physical-balance input, Process link, Upload docs, Notes, Ignore/Mark-OK.

## Xenon comparison (one line)
This is the **most build-ready** bank check — both balances come from the API
(Trial Balance + BankSummary), no raw feed needed. We can match Xenon and add a
**gap → root-cause** link (unreconciled count for that account).
