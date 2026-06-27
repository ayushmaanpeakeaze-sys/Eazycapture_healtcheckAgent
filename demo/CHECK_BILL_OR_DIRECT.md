# Check Spec — Bill or Direct Payment (+ Invoice or Direct Deposit mirror)

What Xenon's "Bill or Direct Payment" does, and our version. This check was
**blocked** (needed bank-transaction data) — now **unblocked** because we wired
the BankTransactions fetch. The sales-side mirror is "Invoice or Direct Deposit".

---

## What it is (Xenon)
Finds a **bank payment coded directly to an account** (not through the supplier
ledger) **while an unpaid bill exists for the same supplier**. Likely the same
spend booked twice → bill stays "unpaid" (profit understated) and the supplier
might get paid twice (cash-flow loss).

## Xenon's features
| Feature | What it does |
|---|---|
| **View in Xero** | deep-link to the unpaid bill or the direct payment |
| **Dismiss Match** | hide a pair that isn't actually an issue |
| **Show Dismissed** | toggle to review dismissed pairs |
| **Bulk** | dismiss many matches |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Direct payment ≤ **X days** after the unpaid bill | **30 days** | max gap (payment date − bill date) to count as a match |

---

## Our current logic (what's actually built) — proxy only
- We only have a **proxy**: an authorised bill with **no bill number** →
  `bill_or_direct_booking` ("may be a direct bank coding"). It does **not**
  actually match a bank spend to an open bill.
- **Built today ≈ ~30%** — proxy, not the real cross-match.

> **Now unblocked:** we just wired `BankTransactions` fetch (spend/receive money
> coded directly). So the real match is buildable.

## Our target logic (the real check)
- Group **open bills** by contact, and **direct bank spends** by contact.
- For contacts that have **both**, match a spend to an open bill when:
  amount ≈ bill outstanding (tolerance) **AND** spend date ≥ bill date **AND**
  within the **30-day** window.
- **Whitelist** legit direct payments (bank charge, salary, GST/TDS, petty cash) so they don't false-flag.

## Edge cases
- **FP — legit direct payment:** bank charge / salary / tax / petty cash → must be **whitelisted**, else every valid payment flags.
- **FP — coincidental same amount:** a genuine separate payment that happens to equal an open bill → keep as **review**, not assert.
- **FP — ambiguous:** contact has several open bills of the same amount → which spend pays which? → review.
- **FN — partial payment:** spend = half the bill → full/near-match misses it.
- **FN — batch payment:** one transfer pays several bills → one-to-one match misses it.
- **FN — advance:** spend dated before the bill → date rule excludes it (mostly correct).

## Configurable settings (target)
| Setting | Xenon | Target for us |
|---|---|---|
| Date window (payment after bill) | 30 days | per-client (default 30) |
| Amount tolerance | (exact) | exact or small tolerance |
| Whitelist accounts | (none documented) | bank charge / salary / GST-TDS / petty cash |

## Logic (pseudo — target)
```python
by_bills  = group_by(open_bills, contact_key)
by_spends = group_by(direct_bank_spends, contact_key)
for contact in (by_bills & by_spends):
    for spend in by_spends[contact]:
        if whitelisted(spend): continue                 # bank charge/salary/tax
        for bill in by_bills[contact]:
            if abs(spend.amount - bill.outstanding) <= TOL and \
               bill.date <= spend.date <= bill.date + WINDOW:
                emit(bill, spend, "payment likely for this open bill")
```

---

## Status (what we have vs to build)
- 🟡 **~30% (proxy):** `bill_or_direct_booking` = authorised bill with no number.
- ❌ **To build (now unblocked):**
  1. Real **bank-spend → open-bill** match (same contact, amount ≈, within 30 days).
  2. **Whitelist** (bank charge/salary/tax/petty cash).
  3. **Invoice or Direct Deposit** mirror (bank **receive** → open **invoice**).
  4. **Actions:** View, Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon matches direct bank payments to open bills (30-day window) for the same
supplier. We had only a proxy (~30%), but the **BankTransactions fetch is now
wired**, so the real match is buildable — plus the whitelist and the
receive-side mirror (Invoice or Direct Deposit).
