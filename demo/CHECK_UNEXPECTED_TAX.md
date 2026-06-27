# Check Spec — Unexpected Tax Code Used

> **Same check as [Unexpected Account Used](CHECK_UNEXPECTED_ACCOUNT.md), on the
> tax field.** Compares each transaction's tax code to the contact's **default
> tax code**. Same approach gap (ours is currently frequency-based → needs
> default-based rewrite), same actions, same enabler (Contact Defaults). This
> page lists only what differs for tax.

---

## What it is (Xenon)
Flags a transaction whose **tax code** ≠ the contact's **default tax code**.
Only fires when a default tax code exists (blank → silent).

**Transactions checked:** customer invoice lines (vs default **sales** tax),
supplier bill lines (vs default **purchases** tax), Money In (sales tax),
Money Out (purchases tax).

## Xenon features & settings — identical to Unexpected Account
Details (Type · Contact · Date · Description · Net Amount · **Tax code used** ·
"Change to" = default tax) · Change→Save (write-back) · Edit-in-Xero (if
reconciled/allocated) · View · Dismiss / Show-Dismissed · Bulk. No threshold.

## What differs from Unexpected Account (only this)
| | Account version | Tax version |
|---|---|---|
| Field compared | account code | **tax code** |
| Our issue type | `unexpected_account` | **`unexpected_tax_code`** |
| Compare against | default account | **default tax code** |
| Severity | MEDIUM (reporting) | **HIGH (VAT return impact)** |
| Compare by | account meaning | **rate/meaning, not label** (TAX001 vs "OUTPUT" can be the same rate → don't false-flag) |

## Our current logic — ⚠️ different approach (same as account)
- Currently `unexpected_tax_code` = **frequency outlier** (tax used once in batch ≥100), **not** default-based.
- **~40% and different** — needs the default-based rewrite.

## Logic (pseudo — target default-based)
```python
for tx in documents:                                    # incl. Money In/Out
    d_tax = contact_default_tax(tx.contact_id, direction(tx))
    if not d_tax: continue                               # no default → silent
    if rate(tx.tax_code) != rate(d_tax):                 # compare by RATE, not label
        emit(unexpected_tax_code, current=tx.tax_code, expected=d_tax, sev=HIGH)
```

## Status (what we have vs to build)
- 🟡 **~40% and different:** frequency-based today.
- ❌ **To build:** rewrite to **default-based** (needs the **2 tax-code defaults** from Contact Defaults — currently we don't even check those), compare by **rate**, include Money In/Out, and the Save / Edit-in-Xero / Dismiss / bulk actions.

## Xenon comparison (one line)
Mirror of Unexpected Account on the tax field — but **HIGH severity** (VAT) and
**compare by rate not label**. Same plan: rewrite to default-based (needs the
tax-code defaults populated first), plus the action buttons.
