# Check Spec — Inactive Contacts

What Xenon's "Inactive Contacts" does, and our version. A cleanup check — find
contacts not used in a long time (or never) so they can be archived to de-clutter
the contact list.

---

## What it is (Xenon)
Identifies Xero contacts **not used for a long time, or at all**, so the user can
archive them.

**Details shown per contact:**
- Contact name
- **Date of most recent transaction**
- **Age** of most recent transaction (in days)

## Xenon's features
| Feature | What it does |
|---|---|
| **View in Xero** | deep-link to the contact to investigate |
| **Archive Contact** | archive (hide) the contact directly from the tool |
| **Dismiss** | hide a contact that shouldn't be archived |
| **Show Dismissed** | toggle to see dismissed/archived; re-check/re-archive |
| **Bulk** | archive many · dismiss many |

## Xenon's settings
Inactivity period (how many days idle = inactive). Doc doesn't state a default;
ours is **180 days**.

---

## Our current logic (what's actually built) — ⚠️ needs upgrade
- Flags active customer/supplier contacts **not appearing in the audited transactions**.
- But it matches by **vendor NAME** (fuzzy) against names in the batch — **not the
  contact's real last-transaction date**. Inactivity window = the audit batch itself.
- `_INACTIVE_DAYS = 180` is only used in the message text.

**Built today ≈ ~45%** — the *idea* works, but it should use each contact's
**actual most-recent-transaction date** (which Xenon shows), not name-matching.

## Edge cases
- **FP — seasonal vendor** (e.g. audit firm used once a year): dormant-looking but legitimate → don't auto-suggest archive; treat as review.
- **FP — brand-new contact** (no transactions yet): looks "inactive" but is new → use **creation date** as a grace.
- **FN — archived-but-recently-used:** an archived contact still being used = process problem → flag separately as HIGH ("archived but used in last 90 days").
- **FN — name mismatch:** name-based matching can miss/false; real last-txn date fixes it.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Inactive-days threshold | (period) | `_INACTIVE_DAYS = 180` (text only) | actually gate on it, per-client |
| New-contact grace | (none) | ❌ | don't flag brand-new contacts (creation date) |
| Archived-but-recently-used | (none) | ❌ | separate HIGH flag |

## Logic (pseudo — target)
```python
last_txn = {}                                   # one pass over all documents
for tx in documents:
    last_txn[tx.contact_id] = max(last_txn.get(tx.contact_id, MIN), tx.date)
for c in active_contacts:
    age = (today - last_txn[c.id]) if c.id in last_txn else INFINITY
    if recently_created(c): continue            # new-contact grace
    if age > INACTIVE_DAYS:
        emit(c, inactive_contact, last_txn=last_txn.get(c.id), age_days=age)
```

---

## Status (what we have vs to build)
- 🟡 **~45%:** flags contacts absent from the batch, but by **name-match**, not real last-txn date.
- ❌ **To build:**
  1. **Real last-transaction date** per contact (one pass) → show date + age (matches Xenon's columns).
  2. **Gate on the 180-day threshold** (per-client), **new-contact grace**.
  3. **Archived-but-recently-used** as a separate HIGH flag.
  4. **Actions:** Archive (deep-link/write-back), Dismiss / Show-Dismissed, **bulk** archive/dismiss.

## Xenon comparison (one line)
Xenon shows each contact's **last-transaction date + age** and lets you
archive/dismiss (incl. bulk). We're at ~45% (name-match). The key upgrade is
computing the **real last-txn date** (one pass) and adding the Archive/Dismiss
actions — straightforward, data is already in hand.
