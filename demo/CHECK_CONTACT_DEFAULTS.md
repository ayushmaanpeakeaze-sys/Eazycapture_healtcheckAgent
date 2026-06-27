# Check Spec — Contact Defaults

Everything Xenon's "Contact Defaults" does, and our version. This is a **setup
helper**, not an error check — but it's the **enabler** for the Unexpected
Account / Unexpected Tax checks (they need defaults to compare against).

---

## What it is (Xenon)
Finds contacts missing **at least one of 4 defaults**:
1. Default **Sales** Account
2. Default **Sales** Tax Code
3. Default **Purchases** Account
4. Default **Purchases** Tax Code

**Why it matters:** when set, Xero pre-fills the account + tax on new
invoices/bills (less human error) AND it **powers Unexpected Account / Unexpected
Tax** — those checks compare each transaction to the contact's default, so a
blank default means those checks stay silent for that contact.

> Not setting a default is **not an error** — just a setup gap. So it belongs in
> a "Suggestions / Setup" area, not the main issue list (it's on most contacts =
> noise).

## Xenon's features
| Feature | What it does |
|---|---|
| **Select defaults** | dropdowns to choose the account/tax code per contact |
| **View in Xero** | deep-link to the contact (see previously-used codes) |
| **Confirm** | write the chosen defaults back to the contact in Xero |
| **Dismiss** | hide a contact (already fine / don't want defaults) |
| **Show all Xero contacts** | toggle to see even contacts that already have defaults (edit them) |
| **Bulk** | dismiss many · confirm many · **set usual sales/purchase account from history** · set usual sales/purchase tax code from history |

## Xenon's settings
None (present/absent). The "smart" part is **suggesting the usual code from history**.

---

## Our detection logic (what's actually built)
- For each active contact: if **supplier** and purchase **default account** blank → flag; if **customer** and sales **default account** blank → flag.
- Emits `contact_defaults` (medium) with the missing list.

**Built today = ~50%** — only the **2 account** defaults; the **2 tax-code**
defaults are **not** checked.

## Its role (important)
This is the **chaabi** for **Unexpected Account / Unexpected Tax (default-based)**.
Our Unexpected checks are currently **frequency-based** (not default-based); to
move them to default-based (like Xenon), Contact Defaults must be populated
first. We also run Multi-Account (history) + AI, so we're not blind when defaults
are blank.

## Edge cases
- **FP — contact that legitimately never uses a default** (every txn a different account): "missing default" is noise → keep it a *suggestion*, not an error.
- **FN — only checking accounts:** blank **tax-code** default is missed → and then Unexpected Tax can't run. Must check all 4.

## Configurable settings (Xenon parity + our extras)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Present/absent of 4 defaults | ✅ (all 4) | account only (2 of 4) | add 2 **tax-code** defaults |
| History-suggest usual code | ✅ | ❌ | suggest most-common account/tax from past docs |
| Min transactions before "expected" | (none) | ❌ | e.g. 3+, so one-off contacts don't nag |

## Logic (pseudo)
```python
for c in active_contacts:
    missing = []
    if c.is_supplier and blank(c.purchases_default_account): missing += 'purchase account'
    if c.is_supplier and blank(c.purchases_default_tax):     missing += 'purchase tax code'   # TODO
    if c.is_customer and blank(c.sales_default_account):     missing += 'sales account'
    if c.is_customer and blank(c.sales_default_tax):         missing += 'sales tax code'       # TODO
    if missing:
        emit(c, contact_defaults, missing, suggest=suggest_from_history(c))   # suggest = TODO
```

---

## Status (what we have vs to build)
- ✅ **Built (~50%):** account-default missing for supplier/customer.
- ❌ **To build:**
  1. **2 tax-code defaults** (sales tax, purchase tax) — completes all 4 (and unlocks default-based Unexpected Tax).
  2. **History-suggest** the usual account/tax from past documents.
  3. **Confirm write-back** to Xero (set the default on the contact) + **bulk set-from-history**.
  4. **Show-all toggle**, Dismiss, bulk dismiss.
  5. Move it to a **"Suggestions / Setup"** section (not the main issue list), so it doesn't drown real issues.

## Xenon comparison (one line)
Xenon checks all 4 defaults, suggests usual codes from history, and writes them
back (single + bulk). We're at ~50% (accounts only). The big wins are the **2
tax-code defaults** (also unblocks default-based Unexpected Tax) and the
**history-suggest + write-back** actions.
