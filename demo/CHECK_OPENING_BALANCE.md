# Check Spec — Opening Balance Differences

What Xenon's "Opening Balance Differences" does, and our version. **Important:
Xenon's version and ours are different concepts** — Xenon compares to **Companies
House**; ours checks a Xero-internal conversion account.

---

## What it is (Xenon)
For a **UK Limited Company**, compares the **Net Assets/Liabilities** in the
bookkeeping software to the **statutory accounts filed at Companies House** on the
same date. A difference means the opening position drifted → everything built on
it is off.

**Details shown:**
- Period Ended (per Companies House)
- Net Assets/Liabilities (per Companies House)
- Net Assets/Liabilities (per bookkeeping software, same date)
- **Difference**

**Potential reasons (6):** final-period adjustments not entered in bookkeeping ·
opening-balance journal not posted · journal posted in the wrong (new) period ·
transactions posted to the old period in error (lock-date not set) · old
transactions deleted/voided instead of credit-noting in the current year ·
negligible rounding.

## Xenon's features
| Feature | What it does |
|---|---|
| **Show Late Transactions** | view the most recent transactions posted to that period (or earlier) — spot mis-posted year-end/opening journals |
| **Dismiss** | hide a difference that needs no fix |
| **Show All Results** | toggle to see dismissed ones |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Minimum difference | **£1** | difference ≥ this triggers a flag (ignore rounding) |

---

## Our logic (what's actually built) — ⚠️ different concept
- We flag transactions posted to a **conversion / historical-adjustment account**:
  code **840** OR an account whose name contains "historical adjustment" /
  "opening balance".
- This is **Xero-internal** — we do **not** compare to Companies House.

**Built today ≈ ~70% (of a different, Xero-only version).** Xenon's real check
needs **external Companies House data** (filed net assets), which we don't fetch.

## Edge cases
- **FP — brand-new business:** no opening balance exists → check shouldn't apply.
- **FP — account 840 not universal:** the conversion code differs per org/region → also match by name, not just code 840.
- **FN — multi-currency opening balances:** evaluate in base currency or the gap is wrong.
- **FN — tax opening balances** (GST receivable/payable) carried from the old system → verify separately.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Conversion/historical account | (n/a) | code 840 + name match | per-client code (keep name fallback) |
| Minimum difference | £1 | (n/a) | add, for the Companies-House version |
| **Companies House comparison** | ✅ core | ❌ | needs a **Companies House API** integration (external) — your decision |

## Logic (pseudo)
```python
# Ours (Xero-internal):
hist = {client_code or 840} ∪ accounts where name ~ 'historical adjustment'/'opening balance'
for tx in documents:
    if tx.account_code in hist: emit(tx, opening_balance_difference, code=tx.account_code)

# Xenon's (external): compare Balance-Sheet Net Assets vs Companies House filed → gap
```

---

## Status (what we have vs to build)
- 🟡 **~70% of a different version:** Xero-internal conversion-account flag.
- ❌ **To build (to match Xenon):**
  1. **Companies House API** integration → fetch filed Net Assets per period.
  2. Compare to Balance-Sheet net assets on the same date → flag the **difference**.
  3. **Min-difference** threshold (£1), **Show Late Transactions**, Dismiss / Show-All.

## Xenon comparison (one line)
Different concepts: Xenon compares bookkeeping Net Assets to **Companies House**
filed accounts (needs external data); ours flags a Xero-internal
conversion/historical account. The Companies-House version is buildable but needs
a **new external integration** — a product decision for sir.
