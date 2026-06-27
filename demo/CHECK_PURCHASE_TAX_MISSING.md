# Check Spec — Purchase Tax Missing

> Buy-side sibling of [Sales Tax Missing](CHECK_SALES_TAX_MISSING.md). Same gate
> (org must be VAT-registered) but a **richer ignore-list + considered-accounts**
> config — that's the important part to copy.

---

## What it is (Xenon)
For a **VAT-registered** org, flags transactions posted to an **Expense / Fixed
Asset Additions / Prepayments** account but given an **'Outside scope' / 'No VAT'**
purchase tax code → input VAT likely missed.

**Checked:** supplier bill lines, **Money Out** (direct payments to an account code).

## Xenon's key config — the ignore-list & considered accounts
**Auto-IGNORED expense accounts** (legit no-VAT, avoid false positives):
Wages & Salaries · Director's Remuneration · Employer's National Insurance ·
Pension Costs · Depreciation · Donations · Rates · Corporation Tax.
*(editable per org.)*

**Balance-sheet accounts auto-CONSIDERED** (normally ignored, but these can carry
input VAT): Prepayments · Office Equipment · Computer Equipment · Buildings ·
Leasehold Improvements · Motor Vehicles · Plant & Machinery · Intangibles.
*(editable per org.)*

## Xenon's features
Details (Type · Contact · Date · Bill ref · Account · Description · Net Amount ·
**Tax code**) · View/Edit (fix in Xero) · Dismiss / Show-Dismissed · Bulk dismiss.

## Xenon's gating
- Org **VAT-registered** (else off).
- **Account-type:** Expense / Fixed Asset Additions / Prepayments.
- Minus the **ignored list**, plus the **considered BS accounts**.

---

## Our logic (what's actually built)
- Flags ACCPAY/expense lines with a **zero/no-VAT** tax code → `purchase_tax_missing`.

**Built today ≈ ~55%.** Gaps: **no org-VAT gate**, **no account-type filter**,
**no ignored-list** (wages/pension/depreciation…), **no considered-BS list** →
noisy.

## Edge cases
- **FP — legit no-VAT spend:** wages, NI, pension, rates, corp tax, donations → must be in the **ignore-list**, else every one flags.
- **FP — unregistered/composition vendor, import (tax at customs):** genuine no-VAT.
- **FN — without gating:** real missing input-VAT drowns in noise.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Org VAT-registered gate | ✅ | ❌ | add |
| Account-type filter (Expense/FA/Prepayment) | ✅ | ❌ | add |
| Ignored expense accounts | ✅ (8 defaults) | ❌ | add per-client list |
| Considered BS accounts | ✅ (8 defaults) | ❌ | add per-client list |

## Logic (pseudo — target)
```python
if not org.is_vat_registered: return []
for tx in expense_docs:                                 # bill lines + Money Out
    if acc_type(tx) not in (EXPENSE, FIXED_ASSET, PREPAYMENT): continue
    if tx.account_code in IGNORED_ACCOUNTS: continue    # wages/pension/depreciation…
    if not zero_vat(tx.tax_code): continue
    emit(tx, purchase_tax_missing,
         HIGH if contact_uses_vat_elsewhere(tx) else MEDIUM)
```

---

## Status (what we have vs to build)
- 🟡 **~55%:** flags zero-VAT on expense lines, ungated.
- ❌ **To build:** org-VAT gate · account-type filter · **ignored-list** (wages/pension/depreciation/rates/corp-tax/donations/NI/director-rem) · **considered-BS list** (prepayments/equipment/buildings/vehicles/plant/intangibles) · View-Edit / Dismiss / bulk actions.

## Xenon comparison (one line)
Mirror of Sales Tax Missing, but the **ignore-list + considered-accounts** is the
real value (kills wages/pension/etc. false positives). We're ~55%; copying those
lists + the org-VAT gate is the main win.
