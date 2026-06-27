# Duplicate Invoices — Complete Logic & Behaviour Guide

Everything about the Duplicate Invoices check: the detection logic line by line,
how the confidence score is built, why the left card is "Original" and the right
is "Duplicate", how every setting changes the results, what each action button
does, and how Xero handles each one (incl. "unallocate to void").

Source of truth: [`_find_duplicate_bills`](app/services/healthcheck/deterministic.py#L482)
and the trapped-row actions in
[`ResolveService`](app/modules/healthcheck/services/resolve_service.py).

---

## 1. What the check does (one line)

> For each **contact**, look at their invoices, and flag any **pair** that looks
> like the same invoice entered twice — scoring how confident we are.

It is **direction-aware**: sales invoices (`ACCREC`) → `duplicate_invoice`;
supplier bills (`ACCPAY`) → `duplicate_bill`. Credit notes are skipped (they're
intentional reversals, not duplicates).

### Duplicate Bills = the SAME engine (not separate logic)

There is **no separate "duplicate bills" code**. Bills and invoices run through
the **one** function `_find_duplicate_bills`. The *only* line that differs is the
label it stamps, decided by document direction:

```python
dup_issue_type = (
    "duplicate_invoice" if type_a in _SALES_DOC_TYPES   # ACCREC = sales
    else "duplicate_bill"                                # ACCPAY = bills
)
```

So **everything** below — blocking, additive points, the hard window filter,
recurring cadence detection, the self-match guard, RAW reference — applies
**identically** to bills and invoices. One engine, two labels.

Verified on bills (`ACCPAY`): same-day bill pair → `duplicate_bill` 0.97 HIGH;
31-day pair at the default same-day (0) window → dropped; a monthly bill series
at a wide window → `duplicate_bill` 0.45 LOW `recurring=true`. (In the test suite the
`_doc` helper defaults to `ACCPAY`, so most duplicate tests already exercise
**bills**.)

---

## 2. The logic, step by step

The engine is **blocking + additive scoring + recurring re-prove**
([`_find_duplicate_bills`](app/services/healthcheck/deterministic.py#L516)):

| Step | What it does | Why |
|---|---|---|
| **1. Eligible docs** | Drop credit notes / typeless rows. | They aren't duplicates. |
| **2. Blocking by contact** | Bucket every invoice by its **contact** (canonical key — merged duplicate-contacts share a bucket via the alias). | The speed trick — see step 3. |
| **3. Candidate pairs** | Only invoices in the **same contact bucket** are paired. Oversized buckets are skipped. | **Never O(n²)** (per-contact, not all-vs-all) **and** never pairs two *different* customers who share a standard fee/reference. |
| **4. Same direction** | Skip if one is `ACCREC` and the other `ACCPAY`. | A sales invoice can't duplicate a bill. |
| **5. Signals** | Compute `same amount`, `exact reference` (**RAW** — case-sensitive, not normalized: `INV-1`≠`INV1`, `Monthly Support`≠`Monthly support`), `same contact`, `days apart`, `within window`. | The raw facts. |
| **6. Gates (the toggles)** | Apply the 3 on/off settings (§4): require-same-amount, require-exact-reference, also-check-paid. | The user's strict/loose knob. |
| **7. Additive score** | Sum the points each signal earns (§3). | "How sure are we?" — one strong signal is enough. |
| **8. Recurring re-prove** | If it's a same-(contact, amount) **series**, compare the gap to the usual cadence (§3b). On-cadence → recurring (LOW); much closer than cadence → real duplicate (HIGH). | A monthly charge isn't a duplicate; a double-entry in one month is. |
| **(applied after step 6) Hard window filter** | Pairs **further apart than `Date within`** are dropped entirely — never scored. | The setting means what its label says: *only pair invoices dated this close together*. `days=0` → only same-day. |
| **10. Confidence floor** | Drop the pair if score < `Min confidence` (default 0.30). | Hide matches weaker than the client wants. |

Whatever survives becomes a flagged duplicate (two rows — original + duplicate).

---

## 3. The confidence score (additive points)

Each signal contributes **independent points**; the (capped) sum is the
confidence ([deterministic.py](app/services/healthcheck/deterministic.py#L482)).
Candidates come from **blocking by contact** (hash index on the canonical key),
so we only score pairs **within the same contact** — never an O(n²) scan, and
never two different customers who happen to share a standard fee/reference.

| Signal matched | Points |
|---|---|
| Same reference (RAW — case-sensitive, not normalized) | **+40** |
| Same amount | **+35** |
| Same contact | **+20** |
| Within the date window | **+15** |

Sum → confidence (cap **97**). **Show if ≥ `Min confidence`** (default **0.30**),
so a single strong signal is enough — shown at a low score for the user to judge.

**`Date within` is a HARD filter** (applied before scoring): a pair further
apart than the window is **dropped entirely** — not a duplicate. So `days=7`
shows only pairs within a week, `days=0` only same-day. Widen the window to also
consider pairs raised further apart.

**Tier / badge:** `≥ 0.80` HIGH · `0.55–0.79` MEDIUM · `< 0.55` LOW (review).

**Same contact is required.** Candidates are generated **per contact** (canonical
key), so two *different* customers are never paired — even if they share a
standard reference + amount (e.g. many clients on a "Monthly Support" £541.25
fee). The only cross-record case is when the Duplicate-Contacts check has
**merged** two records of the *same* entity (via the alias) — they then share the
bucket and the match is tagged `cross_contact`.

Examples: full same-contact match within the window (amount+ref, close) = **97%
HIGH**; same contact + same ref + *different* amount, close = **0.75 MEDIUM**;
two *different* customers with the same fee+ref = **not paired**; a recurring
monthly pair 31 days apart at the default same-day (0) window = **dropped** (too
far) — at a 40-day window it's **downweighted to 0.45 LOW review** by the
recurring detector (§3b).

`match_reasons.points` carries the per-signal breakdown (e.g.
`{contact:20, amount:35, reference:40, date:15}`) so the UI can show *why*.

## 3b. Recurring re-prove (engine intelligence, not a setting)

**The problem settings can never solve:** a recurring monthly charge (same
contact, same amount, same/sequential reference) *looks identical* to a
duplicate. No combination of toggles can separate them — they genuinely match on
amount + reference + contact. So this lives in the **logic**, not the settings.

**The key insight:** a recurring charge has a *usual gap* (monthly ≈ 30 days). A
duplicate is the one that **breaks** that gap — the same period entered twice,
much closer together than the cadence.

```
Monthly series:  21 Mar → 21 Apr → 21 May   (≈30-day gaps)   → NORMAL recurring
Double-entry:    21 Apr  AND  23 Apr         (2-day gap ≪ 30) → real DUPLICATE
```

**How the engine does it** ([deterministic.py](app/services/healthcheck/deterministic.py#L516)):

1. Group invoices by **(contact, amount)** — *not* by reference, because real
   recurring charges keep the amount but get a fresh sequential number each
   period (`NC-2020`, `NC-2021`, …).
2. If a group has **≥3** invoices, learn its **cadence** = the *median*
   consecutive day-gap (median is robust — one stray double-entry gap won't
   skew it).
3. For each pair in the series (that's **inside** the date window — far-apart
   pairs are already dropped by the hard filter, so this matters at wide windows):
   - **gap ≈ a multiple of the cadence** (±25%) → **normal recurring** → capped
     to **0.45 LOW "review"** (shown, never HIGH).
   - **gap ≪ cadence** (much closer than one period) → **same-period
     double-entry = real DUPLICATE** → full points → **HIGH** (as long as it's
     within the date window — a tight window already keeps it close).

**Crucially, recurring is NOT hidden — it's shown at LOW review.** So if the
engine is ever wrong, the user still sees it and nothing slips through (no false
negative). `match_reasons.recurring = true` lets the UI label it *"looks like a
subscription"* and sort it to the bottom.

**Verified on real data:** Net Connect's 5 monthly £132 invoices (sequential
numbers) → all 10 pairs tagged `recurring`, LOW review — they don't flood the
HIGH duplicates list. But add a 2-day double-entry and that one pair jumps to
HIGH.

### Settings vs Logic — two different jobs

| Concern | Solved by |
|---|---|
| *How strict / loose to match* | **Settings** — the user's knob (date / reference / amount / paid / confidence) |
| *Recurring monthly bill isn't a duplicate* | **Logic** — cadence re-prove (above), not a setting |
| *Same invoice matching itself* | **Logic** — a pair is always two distinct `transaction_id`s |

Settings = the user's knob. Logic = the engine's intelligence. You need both.

## 3c. Engine safeguards (edge cases handled in logic)

These are correctness guards that no setting controls — the engine just does the
right thing:

| Guard | What it does | Where |
|---|---|---|
| **No self-match** | A pair is always **two distinct `transaction_id`s**. If malformed data has two rows sharing one ID, they're never paired (no `INV-0024 ↔ INV-0024` against itself). | [deterministic.py](app/services/healthcheck/deterministic.py#L572) |
| **No cross-customer pairing** | Two **different** contacts are never paired — many customers legitimately share a standard fee + reference (e.g. "Monthly Support" £541.25). Only records the Duplicate-Contacts check **merged** (same entity) cross-match, via the alias. | blocking-by-contact |
| **RAW reference** | Reference compare is **case-sensitive, no normalization** (sir's rule): `Monthly Support` ≠ `Monthly support`, `INV-1` ≠ `INV1`. | [deterministic.py:601](app/services/healthcheck/deterministic.py#L601) |
| **Recurring re-prove** | A monthly series isn't flagged HIGH; a same-period double-entry inside it is (see §3b). | §3b |
| **Direction-aware** | A sales invoice (`ACCREC`) is never paired with a bill (`ACCPAY`). | — |
| **Credit notes skipped** | Intentional reversals aren't duplicates. | — |

## 3d. "Latest run wins" — why stale results disappear

Settings and logic changes only take effect on the **next audit run** — the feed
shows the results of the **last** run, persisted in the DB. So if you change a
setting (or we ship a logic fix) and the cards look unchanged, **re-run the
audit**.

On each re-run the engine **reconciles** ([tasks.py](app/modules/healthcheck/tasks.py#L671)):
- a document still flagged → keeps its row (first score preserved);
- a document **no longer flagged** → **auto-cleared** (drops out of the feed,
  kept in the DB for history);
- a row the **user** resolved / dismissed / voided → **left untouched** (sticky).

So after a re-run, stale HIGH cards from an old engine/settings vanish and the
latest run's results show — without undoing anything the user did.

### ⚙️ Where each piece runs — and what to restart (ops note)

Two long-lived processes, two different jobs. Restart the one that owns the code
you changed — **both cache their Python imports at startup**, so edits don't take
effect until the owning process is restarted.

| You changed… | Runs in | Restart |
|---|---|---|
| Duplicate **rules / scoring** (`_find_duplicate_bills` in `deterministic.py`), the **batch endpoint**, `AuditSettings` as the engine reads it | the **API** (`uvicorn app.main:app`) | **API** |
| Worker **orchestration / persist** (`tasks.py`), **contact checks** (`contact_checks.py`) | the **Celery worker** | **worker** |

**Why:** the audit task (`historical_audit`, in the worker) fetches from Xero,
then calls the rules over HTTP (`HEALTHCHECK_AI_BATCH_URL` →
`POST /api/v1/health-check/batch`). The duplicate engine runs **there, inside the
API** — *not* in the worker. The worker only orchestrates fetch → call → persist
(plus contact checks, and only on a *full* run — a duplicates-only run skips them).

> **Recipe after a duplicate-logic change** (`deterministic.py` / `audit_settings.py`):
> 1. Restart the **API**: `pkill -9 -f 'uvicorn app.main:app'` → relaunch.
> 2. Re-run the audit: `POST /sync-xero-history/{company_id}/?scope=duplicates`
>    (or tap **Run duplicates only**).
>
> You do **not** need to restart the worker for a pure rules change. Restart the
> **worker** (`pkill -9 -f 'celery -A app.core.celery_app'` → relaunch) only when
> you change worker-side code (`tasks.py`, `contact_checks.py`).

**Re-run = re-score, with one exception.** A re-run overwrites each row's stored
result with the latest logic + settings — so new scores / tags / messages show up
— **except** rows the user **resolved / dismissed / marked OK**, which are
preserved by design (see §3d). If a re-run still shows stale data, suspect: (a)
the API wasn't restarted, or (b) the row is in one of those end-states. To force a
100%-clean refresh, **wipe + re-audit** — `DELETE` the company's
`health_check_result` rows, then re-dispatch the audit
(`POST /sync-xero-history/{company_id}/`) and poll
`sync-xero-history-status/{batch_id}/` until `completed`.

---

## 4. "Original vs Duplicate" — only for CONFIRMED (high-tier) duplicates

We only label one side `KEEP · ORIGINAL` and the other `DUPLICATE` (and recommend
a void) when we're **confident it's a real duplicate** — i.e. `tier == "high"`
(100% same number, 95% same-day same-amount, or 90% exact reference). For every
weaker **review** pair (recurring 45%, different-amount 65%, no-ref 75%, …) we do
**not** presume which side is the duplicate: `this_is_likely_original` is set to
**`None`**, no void is recommended, and the card just shows the **confidence %**
and asks the user to review.

| `this_is_likely_original` | Card | Action |
|---|---|---|
| `True` | `KEEP · ORIGINAL` | keep this |
| `False` | `DUPLICATE` | void recommended |
| `None` (review pairs) | no original/duplicate tag — just the confidence badge (e.g. `REVIEW (RECURRING) · 45%`) | review only, **no void** |

> With the default **90%** confidence bar, every visible card is high-tier → has
> original/duplicate. The `None` (confidence-only) cards appear only when the user
> **lowers the bar** to review weaker matches — *"original/duplicate for the
> default logic, just a score for everyone else."*

**Which high-tier side is the original** (when we *do* decide), in order:
1. If exactly one side is **paid / bank-matched** → keep that one, void the
   outstanding phantom (the paid copy can't be voided anyway).
2. Else the one **entered later in Xero** (`posted_date`) is the re-entry.
3. Else fall back to the **earlier issue date** = original.

This drives the labels and the "void this one" recommendation; for `None` pairs,
`suggest_fix` returns a **review** suggestion (compare side-by-side), never a void.

---

## 5. The "what matched" chips

Each flag carries a `match_reasons` object ([lines 602-614](app/services/healthcheck/deterministic.py#L602)) that the UI renders as chips:

| Chip | Field | Notes |
|---|---|---|
| `HIGH · 97%` | `tier` + `confidence` | badge + percentage |
| `Same contact` | `same_contact` (always true here) | grouping guarantees it |
| `Same US$541.25` / `£703.63 vs £216.50` | `same_amount`, `amount`, `other_amount`, `currency` | shows both values when they differ |
| `40 days apart` | `days_apart` | |
| `🟢 Same reference` | `reference_match` = `exact` (green) / `none` / `different` | |
| `KEEP · ORIGINAL` / `DUPLICATE` | `this_is_likely_original` | from §4 |
| partner (`INV-0005`, date) | `duplicate_of_invoice_number`, `duplicate_of_date` | the other card |

---

## 6. The settings — how each one changes the results

Settings live per-company in `audit_config['settings']` and are read by the
audit at run time. **Changing a setting does not re-score the existing feed —
you must re-run the audit** (see §8).

> **Defaults are additive.** Out of the box the feed shows only the strong,
> near-certain duplicates; each setting you change **adds** a weaker class of pair
> on top — the default results never disappear. The confidence bar (below) then
> filters whatever is on screen.

### `Date within ___ days` (`duplicate_days_window`, default 0)
- **What:** only compare invoices dated this close together.
- **Default 0 = same day only.** Higher = catch duplicates raised further apart
  (incl. monthly re-entries / recurring); it's a **hard gate** — pairs outside the
  window are never even scored.

### `Require exact reference` (`duplicate_require_exact_reference`, default ON)
- **ON (default):** only pairs with the **exact same reference** (or a strong
  number match) are shown; conflicting-reference pairs are dropped. Keeps the
  default feed precise.
- **OFF:** reference is no longer required — blank-ref and different-ref pairs can
  also surface (scored lower).

### `Require same amount` (`duplicate_require_same_amount`, default ON)
- **ON (default):** only pairs with the **same total** are shown.
- **OFF:** different-amount pairs can surface too (scored 0.20 lower). This is
  how you catch a duplicate with a typo'd amount.

### `Also check paid invoices` (`duplicate_also_check_paid`, default OFF)
- **OFF (default):** at least **one** invoice in the pair must be **unpaid** — so
  **paid + paid pairs are HIDDEN** by default (a charge both copies of which are
  already paid is usually deliberate, not a stray re-entry).
- **ON:** paid + paid pairs are also matched and surfaced. Turn it ON to review a
  genuine duplicate where *both* copies were paid.

### `Min confidence — N%` (`duplicate_min_confidence`, default 0.90) — the Confidence bar
- **What:** hide any pair scoring below this. This is a **post-scoring filter** —
  it never changes which pairs are *found*, only which are *shown*.
- **Default 0.90** surfaces only the near-certain duplicates (100% same number,
  95% same-day same-amount, 90% exact reference). Keep it at 90% for precise
  results.
- **Lower it** to review weaker possible matches the default logic already found
  (75% no-ref, 65% different-amount, 45% recurring) — these show **without** an
  original/duplicate tag, just their confidence (see §4).

### Putting it together — "show me everything, I'll decide"
Loosen every lever (additive — these only *add* weaker classes on top of the
strong defaults):
```
Require exact reference : OFF   ← also surface blank/different-ref pairs
Require same amount     : OFF   ← also surface different-amount pairs (typo'd totals)
Also check paid         : ON    ← also surface paid + paid pairs
Date within             : 60    ← also compare pairs raised up to 60 days apart
Min confidence          : 40%   ← lower the bar to show the weak matches (45-75%)
```

---

## 7. The action buttons — what each does & how Xero handles it

`row_id` = the trapped row's `id`. All actions are `POST /api/v1/health/trapped/{row_id}/<action>/?company_id=…`.

| Button | Endpoint | What it does | Xero side |
|---|---|---|---|
| **View** | — (`xero_url`) | Opens the invoice in Xero. | Deep link, read-only. |
| **Void** | `/void/` | Cancels the duplicate (Status → `VOIDED`). Only offered when the invoice is **unpaid**. | Writes `Status: VOIDED` to Xero (real when connected, else stub). **Blocked** if the invoice has a payment/credit allocated → returns `HAS_PAYMENT_OR_CREDIT` (see §7a). |
| **Credit Note** | `/credit-note/` | Creates a credit note that fully credits the invoice (write-off / discount) and marks the row resolved. | `POST /CreditNotes` (type `ACCRECCREDIT`/`ACCPAYCREDIT`, AUTHORISED, mirrors the invoice's contact + line items) → then `PUT /CreditNotes/{id}/Allocations` to apply it to the invoice. |
| **Dismiss match** | `/dismiss/` | Marks the pair a **false positive** and hides it. | No Xero write — local state only. Re-appears under "Show dismissed". |
| **Ignore (30 days)** | `/snooze/` | Hides the row for N days (default 30); it ages back into the feed afterwards. | No Xero write — local state only. |
| **Mark OK / keep both** | `/mark-ok/` | Accepts the pair as legitimate and hides it. | No Xero write — local state only. |
| **Resolve** | `/resolve/` | Applies allow-listed field updates and marks resolved. | Writes the field updates to Xero (or stub). |
| **Bulk** | `/bulk/` | Apply dismiss / snooze / mark-ok to many rows at once. | Local state only. |

After any action the row drops out of the feed (unless `include_dismissed=true`).

### 7a. Why "Void" is blocked on a paid invoice

[`resolve_service.py:200-213`](app/modules/healthcheck/services/resolve_service.py#L200):

```python
if status_str == "PAID" or paid_val > 0:
    error_code = "HAS_PAYMENT_OR_CREDIT"
    error_detail = ("This invoice has a payment or credit note allocated and "
                    "can't be voided. Unallocate it in Xero first, then void.")
```

So a **paid duplicate** shows **"Paid — unallocate to void"** instead of a Void
button — both in the UI and enforced by the API. This is a safety stop.

---

## 8. "Unallocate to void" — what it means

Three terms:
- **Void** = cancel an invoice so it's removed from the books (used for a duplicate).
- **Allocate** = a payment (or credit note) *matched/applied* to an invoice. Paying INV-0021 "allocates" that payment to it.
- **Unallocate** = *detach* that payment from the invoice; the money becomes an **unallocated credit** on the customer's account (it doesn't vanish).

**Why Xero blocks voiding a paid invoice:** voiding it would orphan the payment
(£X pointing at nothing), breaking the books. So you must deal with the money
first.

**The real steps for a paid duplicate (e.g. Rex Media INV-0021, paid £541.25):**
1. **Unallocate** the £541.25 from INV-0021 → it becomes a floating credit on Rex Media.
2. **Void** the now-unpaid INV-0021 (duplicate removed).
3. **Re-allocate** the £541.25 to the real invoice INV-0004 (where the money was meant to go).

That's exactly what "Paid — unallocate to void" is telling you to do.

---

## 9. The full flow (settings → see data → fix)

```
1. GET  /audit-config/         → render the Duplicate Invoices settings section
2. PUT  /audit-config/         → save changed settings (only changed keys)
3. POST /sync-xero-history/{company_id}/   → re-run the audit with new settings
4. GET  /sync-xero-history-status/{batch_id}/ → poll until done
5. GET  /trapped-invoices/     → the duplicate cards (result.flagged[].match_reasons)
6. Act per card: Void / Credit Note / Dismiss / Ignore / Mark OK
```

> Reminder: settings only take effect after step 3 — saving alone does not
> recompute the existing feed.
