# Check Spec — Duplicate Contacts

Everything Xenon's "Duplicate Contacts" does — features, helper data, settings —
and our version (where we're **stronger** than Xenon). Runs **first** in the
audit: every other check groups by ContactID, so a party split across two
records breaks them all.

---

## What it is (Xenon)
Finds contacts (customers/suppliers) added **more than once in error**. Causes:
1. App integrations creating a contact with slightly different spelling.
2. CSV import with a different spelling of the name.
3. No check whether the contact already exists → added again.

Effect: matching payments, finding clients, handling queries all get harder when
one real party is split across records.

**Xenon's method:** name-**similarity %** only — scores name closeness, sorts
highest matches on top.

## Xenon's features
| Feature | What it does |
|---|---|
| **Similarity % score** | name-closeness %, list sorted high→low |
| **Contact Data columns** (to pick which to keep) | Invoices? · Bills? · Person? · Email? · Address? · Telephone? — shows which record is actively used |
| **View / Merge & Archive** | deep-link to the contact in Xero to investigate / merge / archive |
| **Archive** | start archiving the dead contact (done in Xero) |
| **Merge** | move one contact's history into the main one, then remove the secondary (done in Xero) |
| **Dismiss Match** | hide a pair as "not a duplicate" |
| **Show Dismissed** | toggle to review dismissed pairs |
| **Bulk** | dismiss many matches at once |

## Xenon's settings
| Setting | Default | Meaning |
|---|---|---|
| Name similarity ≥ **X%** | **70%** | min name closeness to show as a match |

---

## Our detection logic (what's actually built) — stronger than name-only
- **Stage 1 — blocking (cheap):** bucket contacts by **tax number, bank account, email, phone**, and the **first token of the name**. Only compare within a bucket (kills O(n²)).
- **Stage 2 — weighted score** per pair:
  `tax 0.45 · bank 0.35 · email 0.30 · phone 0.15 · name 0.10×similarity`.
  Flag if total ≥ threshold (one strong signal alone passes; a weak one needs a second).
- **Stage 3 — reprove:** if both have a tax number and they **differ → reject** (different business, even if names match 100%); if one is **customer-only and the other supplier-only → "review, don't merge"** (intentional split, low severity).
- **Helper columns** on every flagged record (has_invoices, has_bills, email, phone, has_address) so a human picks which to keep — **never auto-merge**.
- Name normalisation (`Ltd`=`Limited`) before fuzzy compare. Phone normalised (`+44` vs `0`).

### Why ours is stronger
Xenon detects on **name similarity only** (tax/bank/email shown only to help
*decide*, not to *detect*). We fold **tax/bank/email/phone into the detection
score**, so "Acme Ltd" vs "Acme Stationers" (different names, same VAT) is caught
— which name-only misses.

## Edge cases
- **FP — same name, different company** (two "John Traders"): tax-differ dealbreaker stops it.
- **FP — branches** ("Tesco Manchester" vs "Tesco London"): different entities, similar name → review not merge.
- **FP — one party that's both customer & supplier:** customer/supplier split → review note, not a merge.
- **FP — shared `info@` / reception phone:** should ignore generic email/phone *(not yet built — see gap)*.
- **FN — name fully changed** ("Facebook" → "Meta"): name won't match, but tax/bank/email/phone still catch it; if no shared identifier at all → human review.
- **FN — `Ltd` vs `Limited`, `&` vs `and`:** handled by name normalisation.

## Configurable settings (Xenon parity + our extras)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Name similarity threshold | 70% | `_NAME_SIM_MIN = 0.70` (const) | per-client |
| Signal weights / flag threshold | (none) | hardcoded | per-client (optional) |
| Tax-number dealbreaker | (none) | ✅ ON | keep |
| Generic email/phone ignore | (none) | ❌ not built | add (shared info@/office number) |

## Logic (pseudo)
```python
# STAGE 1 — buckets (cheap, no all-pairs)
buckets = {f: group(active, f) for f in (TAX, BANK, EMAIL, PHONE)} + name_first_token
# STAGE 2 — score within bucket
W = {tax:0.45, bank:0.35, email:0.30, phone:0.15, name:0.10}
for (a, b) in unique_pairs(buckets):
    if a.tax and b.tax and a.tax != b.tax: continue          # different business → reject
    score = sum(W[f] for f in matched(a, b)) + W['name']*name_sim(a, b)
    if score < FLAG_THRESHOLD: continue
    if customer_only(a) and supplier_only(b): emit_split(a, b)  # review, don't merge
    else: emit(a, b, score, helper={invoices, bills, email, phone, address})
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~90%) — stronger than Xenon:** blocking, weighted score (tax/bank/email/phone/name), tax-differ reject, customer/supplier split, helper columns, name+phone normalisation. (Verified live on seed: Acme/email, Initech/phone, Wayne/bank, Umbrella/tax pairs.)
- ❌ **To build:**
  1. **Generic email/phone ignore** (shared info@/office number).
  2. **"Person" helper column** (contact person name) — we have email/phone/address/invoices/bills.
  3. **Per-client** similarity threshold (+ optional weights).
  4. **Actions:** Merge/Archive deep-link to Xero, Dismiss / Show-Dismissed, **bulk** dismiss.

## Xenon comparison (one line)
Xenon detects on **name similarity only** (70%); we detect on **tax + bank +
email + phone + name (weighted)** → more precise, catches renamed-but-same
parties. Gaps are the **action buttons** (Merge/Archive/Dismiss/bulk),
**generic-contact ignore**, and making the threshold per-client.
