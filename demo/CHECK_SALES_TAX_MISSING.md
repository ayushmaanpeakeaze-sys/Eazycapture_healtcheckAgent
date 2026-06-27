# Check Spec — Sales Tax Missing (+ Purchase Tax Missing mirror)

What Xenon's "Sales Tax Missing" does, and our version. Only relevant for
VAT/sales-tax-**registered** orgs. (Purchase Tax Missing is the buy-side
sibling — see CHECK_PURCHASE_TAX_MISSING.md for its richer ignore-list.)

---

## What it is (Xenon)
For an org **registered for sales tax**, flags transactions posted to a **Sales
or Other Income** account but given an **'Outside the scope' / 'No VAT'** tax code
→ VAT likely missed on income.

**Checked:** customer invoice lines, **Money In** (direct deposits to a Sales/Other
Income account).

**Legit exception:** e.g. **Grant Income** (UK, some cases) is genuinely
outside-scope → verify, not auto-wrong.

## Xenon's features
| Feature | What it does |
|---|---|
| **Details** | Type · Contact · Date · Invoice ref · Account · Description · Net Amount · **Tax code used** |
| **View/Edit** | open the transaction in Xero to fix the tax code |
| **Dismiss / Show Dismissed** | hide "actually correct"; toggle to review |
| **Bulk** | dismiss many |

## Xenon's gating (the important part)
- **Org must be VAT/sales-tax registered** — else the check doesn't run.
- **Account-type filter:** only **Sales / Other Income** accounts.
- (No numeric threshold; it's gate + account-type + zero-VAT code.)

---

## Our logic (what's actually built)
- Flags ACCREC/income lines with a **zero/no-VAT** tax code → `sales_tax_missing`.

**Built today ≈ ~55%.** Gaps: **no org-VAT gate**, **no Sales/Other-Income
account-type filter**, **no legit-exception ignore-list** → noisy.

## Edge cases
- **FP — unregistered / composition-scheme** sale, or genuine outside-scope (grant income) → without the org-VAT gate + exceptions, these false-flag.
- **FN — without gating:** if we don't gate, non-taxable items flood the list and the real missing-VAT drowns in noise.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Org VAT-registered gate | ✅ | ❌ | add (skip whole check if not registered) |
| Account-type filter (Sales/Other Income) | ✅ | ❌ | add |
| Outside-scope exceptions | (implicit) | ❌ | per-client (e.g. grant income) |

## Logic (pseudo — target)
```python
if not org.is_vat_registered: return []
for tx in income_docs:                          # invoice lines + Money In
    if acc_type(tx) not in (SALES, OTHER_INCOME): continue
    if not zero_vat(tx.tax_code): continue
    emit(tx, sales_tax_missing,
         HIGH if contact_uses_vat_elsewhere(tx) else MEDIUM)
```

---

## Status (what we have vs to build)
- 🟡 **~55%:** flags zero-VAT on income lines, but **ungated** (noisy).
- ❌ **To build:**
  1. **Org VAT-registered gate** (skip if not registered).
  2. **Account-type filter** (Sales / Other Income only).
  3. **Outside-scope exceptions** (grant income etc.).
  4. **Actions:** View/Edit, Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon gates on **registered + Sales/Other-Income account**, with outside-scope
exceptions. We're ~55% (ungated). The wins are the **gate + account-type filter +
exceptions** (kills false positives) and the Dismiss/bulk actions.
