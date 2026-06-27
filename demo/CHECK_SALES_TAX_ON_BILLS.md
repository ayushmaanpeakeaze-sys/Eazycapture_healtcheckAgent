# Check Spec — Sales Tax on Bills (+ Purchase Tax on Invoices mirror)

What Xenon's "Sales Tax on Bills" does, and our version (built ~85%). Catches a
**wrong-direction tax code** — a sales/output tax used on a purchase. Purchase
Tax on Invoices is the mirror (input tax on a sale).

---

## What it is (Xenon)
Flags **supplier bills** posted with a **sales tax code** (e.g. "20% (VAT on
Income)", "Exempt Income"). The amount looks right but the VAT return goes wrong
(input claim becomes output).

**Checked:** supplier bill lines. **OPTIONAL: Money Out** — but **OFF by default**
(bank payments legitimately use a sales tax code for sales **refunds**).

## Xenon's features
| Feature | What it does |
|---|---|
| **Details** | Type · Contact · Date · Bill ref · Account · Description · Net Amount · **Tax code** |
| **View/Edit** | open in Xero to fix the tax code |
| **Show Bank Payments Too** | toggle to also eyeball direct bank payments (not in the issue count) |
| **Dismiss / Show Dismissed** | hide "actually correct"; toggle to review |
| **Bulk** | dismiss many |

## Xenon's settings
- **Include Money Out?** default **OFF** (refunds legitimately use sales tax) → if off, a "Show Bank Payments Too" toggle appears.

---

## Our logic (what's actually built)
- For each **ACCPAY** line, look up the tax code's **`CanApplyToExpenses`** flag
  (authoritative, from Xero). If it **can't apply to expenses** (i.e. it's an
  output/sales tax), flag `sales_tax_on_bills`. Scans **every line**, not just line 1.
- **Strength:** uses Xero's flag, **not** name-matching ("GST" vs "GST Free").

**Built today ≈ ~85%.** Gaps: **no reverse-charge whitelist**, **no Money-Out
scan/toggle**.

## Edge cases
- **FP — reverse charge / import of services:** a purchase legitimately carrying an output-style tax → needs a **whitelist**.
- **FN — name-based classification** would miss similar names → we avoid this by using Xero's `CanApplyToExpenses` flag.
- **Root cause:** many wrong-tax lines on one account → the account's **default tax** is probably wrong → add a summary flag on that account.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Tax classification source | (Xero flags) | ✅ `CanApplyToExpenses` | keep (our strength) |
| Reverse-charge whitelist | (none) | ❌ | add |
| Include Money Out | toggle (default off) | ❌ | add bank-payment scan + toggle |

## Logic (pseudo)
```python
tax_meta = fetch_tax_rates()                  # CanApplyToExpenses / CanApplyToRevenue
for tx in documents:
    for line in tax_lines(tx):                 # every line
        if whitelisted_reverse_charge(tx, line.tax_code): continue
        if is_purchase(tx) and not tax_meta[line.tax_code].can_apply_to_expenses:
            emit(tx, sales_tax_on_bills, line=line, expected='input/purchase tax')
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~85%):** all-line scan via `CanApplyToExpenses` (flag-based, robust).
- ❌ **To build:** reverse-charge **whitelist**, **Money-Out** scan + "Show Bank Payments Too" toggle, same-account **summary flag**, View-Edit / Dismiss / bulk actions.

## Xenon comparison (one line)
We match the core and our **flag-based** classification (Xero `CanApplyToExpenses`)
is stronger than name-matching. Gaps = reverse-charge whitelist + the optional
Money-Out scan + actions. **Purchase Tax on Invoices** is the mirror: an ACCREC
line whose tax code **can't apply to revenue** (`CanApplyToRevenue=false`) →
`purchase_tax_on_invoices`; optional **Money In** scan, off by default (purchase
refunds legitimately use a purchase tax).
