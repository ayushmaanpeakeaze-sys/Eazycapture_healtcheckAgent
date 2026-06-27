# Check Spec — Multi-Account Suppliers (+ Multi-Tax sibling)

What Xenon's "Multi-Account Suppliers" does, and our version. HISTORY-based:
compares a supplier's transactions to the **accounts it usually uses** (not a
default). Multi-**Tax** Suppliers is the same check on the tax field.

---

## What it is (Xenon)
Flags supplier contacts whose transactions are coded to **more than one account**
— the odd one is often a coding slip.

**Checked:** supplier bill lines, Money Out — vs the supplier's **previously-used
purchase accounts**.

**Different from Unexpected Account:** Multi-Account compares to the contact's
**own history** (what it usually uses); Unexpected compares to the **saved
default**. (We have both; this is the history one.)

## Date range (key feature)
Issues show for the **selected period**, but it also looks back **3 months prior**
(configurable) to learn the "usual" account — so a supplier that's *all*
mis-coded in the current period still gets caught against history.

## Xenon's features
| Feature | What it does |
|---|---|
| **Details** | Contact · Type · Date · Reference · Value · Account · Description |
| **View/Edit in Xero** | edit the bill/payment in Xero (no in-app save for this check) |
| **Mark as "OK"** / **Mark all OK** | accept a txn (or all for a contact) → hide |
| **Show items marked OK** + **Mark as Not OK** | toggle to review/restore |
| **Bulk** | mark contacts OK in bulk |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Lookback months | **3** | how far back to learn the usual account |

---

## Our logic (what's actually built)
- Group docs by **contact**; need ≥ **3** txns (`_SUPPLIER_PATTERN_MIN_TXNS`).
- Find the **dominant** account; require its share ≥ **70%** (`_SUPPLIER_PATTERN_DOMINANCE`).
- Flag the **non-dominant** postings, suggesting the dominant account.

**Built today ≈ ~80%.** Gap: we only use the **current batch**, **no 3-month
lookback**; no multi-category whitelist; compare by code not meaning.

## Edge cases
- **FP — multi-category vendor** (Amazon, a sub-contractor doing plumbing+electrical): genuinely many accounts → **whitelist** or low severity.
- **FP — CoA mid-year restructure** (code 421 → 420): looks multi-account but it's a code change, not an error.
- **FN — all mis-coded this period:** without the lookback, a contact 100% on a wrong account this period shows no "dominant vs outlier" → 3-month history fixes it.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Lookback months | 3 | ❌ (current batch only) | add lookback |
| Dominance threshold | (implicit) | `0.70` const | per-client (70–80%) |
| Min transactions | (implicit) | `3` const | per-client |
| Multi-category whitelist | (none) | ❌ | add (Amazon-type vendors) |

## Logic (pseudo)
```python
by_contact = group_by(documents, contact_key)      # + lookback months (todo)
for txns in by_contact:
    if len(txns) < MIN_TXNS: continue
    dominant = most_common(account for tx in txns)
    if share(dominant) < DOMINANCE: continue        # no clear usual → skip
    for tx in txns where meaning(tx.account) != meaning(dominant):
        emit(tx, multi_account_supplier, suggested=dominant)
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~80%):** per-contact dominant-account pattern, flag outliers, suggest dominant.
- ❌ **To build:**
  1. **3-month lookback** (compare current period to prior history).
  2. **Multi-category vendor whitelist** + compare by **meaning** not raw code.
  3. **Per-client** dominance / min-txns / lookback.
  4. **Actions:** View/Edit, Mark-OK / Show-OK / Mark-Not-OK, bulk.

## Xenon comparison (one line)
Both HISTORY-based (vs the contact's own usual accounts, not the default). We're
~80%; main gaps are the **3-month lookback** and the **multi-category whitelist**,
plus the Mark-OK/bulk actions. Multi-**Tax** Suppliers = same check on tax codes.
