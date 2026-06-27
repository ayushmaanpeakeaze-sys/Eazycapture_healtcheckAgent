# Check Spec — Unexpected Account Used

What Xenon's "Unexpected Account Used" does, and our version — **note: our
current approach is different** (frequency-based) and needs reworking to
default-based to match Xenon. (Unexpected **Tax Code** is the same check on the
tax field.)

---

## What it is (Xenon)
Flags a transaction whose account is **not the contact's DEFAULT account**.
- Only fires when a **default exists** for that contact (blank default → silent).
- Catches coding slips: this contact's "usual" account wasn't used.

**Transactions checked:**
| Transaction | Compared against |
|---|---|
| Customer invoice line | contact's default **sales** account |
| Supplier bill line | contact's default **purchases** account |
| Money In (bank receive) | default **sales** account |
| Money Out (bank spend) | default **purchases** account |

**Different from Multi-Account Suppliers:** Multi-Account compares to the
contact's **other transactions** (history); this compares to the **saved default**.

## Xenon's features
| Feature | What it does |
|---|---|
| **Details shown** | Type · Contact · Date · Description · Net Amount · Account used · **"Change to" (the default)** |
| **Change account → Save** | write the corrected account back to Xero (top option = the default; or pick any) |
| **Edit in Xero** | if the txn is reconciled / has payment / credit allocated, Xero API won't allow the change → deep-link to edit in Xero |
| **View in Xero** | deep-link to the transaction |
| **Dismiss / Show Dismissed** | hide "actually correct" items; toggle to review |
| **Bulk** | dismiss many |

## Xenon's settings
None — it's a straight compare-to-default (no threshold). The **enabler** is
Contact Defaults being populated.

---

## Our current logic (what's actually built) — ⚠️ different approach
- We emit `unexpected_account` as a **frequency outlier**: an account used **only
  once** in a batch that's dominated by another (gated to large batches ≥100).
- This is **NOT** Xenon's default-based check — it doesn't reference the contact's
  default account at all.

**Built today ≈ ~40%, and conceptually different.** To match Xenon we must
**rewrite it to default-based.**

## Edge cases (for the default-based version)
- **FP — legit different account:** one supplier with normal spend + a one-off capital item → differs from default but correct → keep as **review**, not error.
- **FP — label vs meaning:** an account/tax that's the same *meaning* under a different label → compare by **meaning/rate**, not the label string.
- **FN — the default itself is wrong** (archived/opposite): then every correct txn gets flagged. → don't rely on default alone; also run **Multi-Account (history)** + **AI**.
- **FN — blank default:** that contact's miscodings are invisible here → caught by history/AI instead.

## Configurable settings (target)
| Setting | Xenon | Target for us |
|---|---|---|
| Compare-to-default | ✅ | rewrite to this (currently frequency) |
| Archived-default skip | (implicit) | skip if default points to a dead account |
| Compare by meaning, not label | (implicit) | yes (avoid TAX001 vs OUTPUT false flags) |
| Severity | — | account mismatch MEDIUM (reporting); tax mismatch HIGH (VAT) |

## Logic (pseudo — target default-based)
```python
for tx in documents:                              # incl. Money In/Out (bank txns)
    d_acc = contact_default_account(tx.contact_id, direction(tx))
    if not d_acc or archived(d_acc): continue     # no default → silent
    if meaning(tx.account_code) != meaning(d_acc):
        emit(unexpected_account, current=tx.account_code, expected=d_acc, sev=MEDIUM)
```

---

## Status (what we have vs to build)
- 🟡 **~40% and different:** current `unexpected_account` is **frequency-based**, not default-based.
- ❌ **To build:**
  1. **Rewrite to default-based** — compare each txn's account to the contact's default (requires **Contact Defaults populated** — the enabler).
  2. Include **Money In/Out** (bank transactions — now fetched).
  3. Compare by **meaning**, skip **archived** defaults.
  4. **Actions:** Change-account → Save (write-back), Edit-in-Xero (reconciled/allocated), Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon is **default-based** (txn vs contact default); ours is currently
**frequency-based** — a different check. Plan: rewrite to default-based (needs
Contact Defaults filled), add Money In/Out, and the Save/Edit-in-Xero actions.
Meanwhile **Multi-Account (history)** + **AI** already catch a lot of the same
miscoding, so we're not blind today.
