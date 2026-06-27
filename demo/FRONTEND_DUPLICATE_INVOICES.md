# Frontend — Duplicate Invoices / Bills / Credit Notes

Self-contained guide to build the **Duplicate detection** page. How to fetch, the
pair-card data model, the badge + confidence, the bank-matched **risk** signal,
the actions, and the per-client settings. Last updated 2026-06-18.

---

## 0. Basics

| Thing | Value |
|---|---|
| Base path | `/api/v1/health` |
| Auth | `Authorization: Bearer <JWT>` on every request |
| Tenancy | `?company_id=<uuid>` query param on every route (path segment only on dispatch) |
| Money | strings in JSON (`"1250.00"`) |
| ⚠️ Action URLs | use the **row `id`** (not `document_id`) |
| Badge | render the **tier** (High/Medium/Low) — you MAY also show the **confidence %** (e.g. "95%") |

Three duplicate issue types, one engine, **type-aware** (an invoice never pairs
with a credit note):

| `issue_type` | Compares |
|---|---|
| `duplicate_invoice` | ACCREC ↔ ACCREC |
| `duplicate_bill` | ACCPAY ↔ ACCPAY |
| `duplicate_credit_note` | ACCRECCREDIT ↔ ACCRECCREDIT, ACCPAYCREDIT ↔ ACCPAYCREDIT |

Each duplicate makes **two rows** (both sides flagged), linked via
`duplicate_of_transaction_id`. Render them as **one pair card**; the row with
`this_is_likely_original:true` is the keeper, the other is the one to void.

---

## 1. Get the data

### Option A — fast duplicates-only audit
`POST /sync-xero-history/{company_id}/?scope=duplicates`
→ `{ "batch_id": "…", "status": "in_progress" }`. Poll
`GET /sync-xero-history-status/{batch_id}/` until `status:"completed"`.

### Option B — read the feed
`GET /trapped-invoices/?company_id=...`
Query: `limit`, `offset`, `search`, `include_dismissed` (`true` = "Show dismissed").
Keep items whose `result.flagged[].issue_type` ∈ the three duplicate types.

---

## 2. Data model — one row

```json
{
  "id": "9a3f…",                 // ROW id → use in every action URL
  "document_id": "c1d2…",
  "document_type": "ACCREC",     // ACCREC | ACCPAY | ACCRECCREDIT | ACCPAYCREDIT
  "status": "blocked",
  "title": "Northgate Solutions has duplicate invoices",
  "xero_url": "https://go.xero.com/…",
  "result": {
    "flagged": [ FlaggedIssue ],
    "vendor_name": "Northgate Solutions",
    "invoice_number": "INV-3300",        // org's own number
    "xero_reference": "PO-7781",         // Xero Reference field
    "details": "Project Atlas",
    "amount": "2400.00",
    "currency_code": "GBP",
    "invoice_date": "2026-03-05",        // issue date
    "due_date": "2026-03-28",
    "posted_date": "2026-03-05",
    "amount_due": "0.00",
    "amount_paid": "2400.00",
    "invoice_status": "PAID",
    "reconciled": true,                  // BANK MATCHED (see §4) — true | false | null
    "resolved": false, "dismissed": false, "marked_ok": false, "snoozed_until_ts": 0
  }
}
```
**Per-row card labels** (so the user knows what each value is — always show the label):

| Card label | Field | Example |
|---|---|---|
| Invoice no. | `invoice_number` | INV-3300 |
| **Issue date** | `invoice_date` | 5 Mar 26 |
| Amount | `amount` + `currency_code` | £2,400.00 |
| Status | `invoice_status` + `amount_due` | **Paid** / **Unpaid** (use one word — "Unpaid", not "Outstanding") |
| Bank reconciled ✓ | `reconciled` | (true) |
| Reference | `xero_reference` | PO-7781 |
| **Description** | `details` | Project Atlas |

> Label the date **"Issue date"** and the `details` line **"Description"** so the
> user knows what each value is (don't show the bare value alone).

### 2.1 `FlaggedIssue`
```json
{
  "issue_type": "duplicate_invoice",
  "severity": "critical",                // critical | high | medium  (see §3.3)
  "message": "Likely duplicate of INV-3300 (…). Recommended: void this one, keep INV-3300.",
  "confidence": 1.0,                     // 0–1 — may render as "100%"
  "duplicate_of_transaction_id": "b0a9…",
  "duplicate_of_invoice_number": "INV-3300",
  "duplicate_of_date": "2026-03-05",
  "this_is_likely_original": false,      // true = keep this one, void the sibling
  "match_reasons": { … see §3 … }
}
```

---

## 3. Confidence, tier & "what matched" — `match_reasons`

```json
{
  "same_contact": true,
  "same_amount": true,
  "amount": "2400.00",
  "other_amount": null,                  // set only when amounts differ
  "currency": "GBP",
  "days_apart": 0,
  "reference_match": "exact",            // exact | none | different
  "same_invoice_number": true,
  "distinct_documents_possible": false,  // different invoice numbers → see §3.2
  "cross_contact": false,                // matched across 2 merged contact records
  "confidence": 1.0,                     // 0–1
  "review": false,                       // true when tier != high
  "tier": "high",                        // high | medium | low
  "recurring": false,                    // subscription cadence → likely not a duplicate
  "one_paid_one_outstanding": true,      // RISK: one side paid, one outstanding
  "one_reconciled_one_outstanding": true,// RISK (stronger): one bank-matched, one outstanding
  "risk": "high"                         // high | normal  (see §4)
}
```

### 3.1 Confidence model (sir's rules)
The engine assigns a fixed confidence per match pattern (default window = **0 days
= same issue date**):

| Pattern | Confidence | Tier |
|---|---|---|
| Same invoice number + amount, same day (everything matches) | **1.0** (100%) | high |
| **Different** invoice number, **same day**, rest same | **0.95** | high  ← + "2 distinct?" note |
| Same reference + amount, **no** invoice number, same day | 0.90 | high |
| Different invoice number with a **day gap** (window widened) | 0.70 | medium |
| Different **amount** ("require same amount" off) | 0.65 | medium |
| Weak — only amount + customer + same day | 0.75 | medium |
| Recurring / subscription cadence | 0.45 | low |

Badge: `tier` → 🔴 high "Likely duplicate" · 🟡 medium "Possible — review" ·
⚪ low "Review (recurring)". You may also print the `confidence` as a %.

### 3.2 "Could be 2 distinct documents" note
When the two **invoice numbers differ**, `distinct_documents_possible:true` and the
`message` ends with *"Can be two distinct documents, please check."*
- **Same day** → still **95% high** (Xero auto-numbers re-entries, so different
  numbers on the same day are usually still a duplicate — show the note as a caution).
- **Day gap** (only when the window is widened) → drops to **70% review** (a gap
  makes genuinely separate documents more plausible).

### 3.3 Severity
`critical` = a high-tier duplicate where money already moved on one side
(`risk:"high"`). `high` = high-tier, normal risk. `medium` = everything else.

---

## 4. 🏦 Bank-matched (the RISK signal)

Two different things, both on each row:

| Field | Means | Source |
|---|---|---|
| **Paid** (`invoice_status="PAID"` / `amount_due=0`) | a payment was recorded | comes with the invoice |
| **Bank reconciled** (`reconciled=true`) | that payment was **matched to a bank statement line** — money actually moved | Xero `Payments.IsReconciled` |

`reconciled` is `null` when payments weren't fetched for that audit.

**Why it matters:** the most dangerous duplicate is where **one copy is paid (and
bank-reconciled) while the other is still outstanding** — money already went
out/in on one of them. The engine surfaces this:
- `match_reasons.one_paid_one_outstanding` — one side paid, the other outstanding.
- `match_reasons.one_reconciled_one_outstanding` — stronger: one bank-matched, one outstanding.
- `match_reasons.risk` = `"high"` in that case → show a **⚠️ "One already paid — high risk"** badge and sort these to the top.

`risk:"high"` does NOT change WHETHER it's a duplicate (both-unpaid duplicates still
flag) — it only flags *urgency*.

---

## 5. Actions (all `POST`, all take `?company_id=`, use the row `id`)

| Button | Route | Notes |
|---|---|---|
| **Void** | `POST /trapped/{row_id}/void/` | no body. Credit notes void via `/CreditNotes/` automatically. **Paid guard** below. |
| **Dismiss** | `POST /trapped/{row_id}/dismiss/` | `{ "dismissal_reason": "…" }` → `{ row_id, dismissed:true }` |
| **Snooze** | `POST /trapped/{row_id}/snooze/` | `{ "days":30, "reason":"…" }` |
| **Mark OK** | `POST /trapped/{row_id}/mark-ok/` | `{ "reason":"…" }` |
| **Bulk** | `POST /trapped/bulk/` | `{ row_ids:[…], action:"dismiss"\|"snooze"\|"mark_ok", days?, reason? }` |
| **Suggest fix** | `GET /trapped/{row_id}/suggest-fix/` | one-click AI suggestion (void_duplicate) |
| **Show dismissed** | `GET /trapped-invoices/?include_dismissed=true` | |

**Void paid guard** — blocked when a payment/credit is allocated → HTTP 400
`{ "error_code": "HAS_PAYMENT_OR_CREDIT", "error_detail": "Unallocate … first." }`.

---

## 6. Per-client settings (Audit Configuration)

`GET /audit-config/?company_id=...` → `settings_schema` has a **"Duplicates"** group;
save via `PUT /audit-config/` under `settings`. Render each field from
`settings_schema` (`{key,label,type,help,unit,min,max,step,default}`).

| key | type | meaning | default |
|---|---|---|---|
| `duplicate_days_window` | int (days) | how many days apart the two docs may be dated. **0 = same issue date only**; raise to 1/2/… | `0` |
| `duplicate_require_same_amount` | bool | both must have the same total (off → different amounts also surface) | `true` |
| `duplicate_require_exact_reference` | bool | drop pairs whose references **conflict** (no-ref pairs still match) | `true` |
| `duplicate_also_check_paid` | bool | include already-paid invoices (off → at least one must be unpaid) | `false` |

> No confidence/threshold knob in the UI — confidence is computed (§3.1).

---

## 7. Original vs duplicate
The row with the **later `posted_date`** (when it entered Xero) is the re-entry →
`this_is_likely_original:false`. Falls back to `invoice_date` when `posted_date`
is missing/equal. Only decides which row is *labelled* original vs duplicate.

---

## Recent changes (2026-06-18)
- **Default window 0** (same issue date). Configurable to 1/2/N days.
- **Rule-based confidence** (§3.1): same number→100%, diff number same-day→95%,
  exact-ref-no-number→90%, weak→75%, recurring→45%, diff-amount→65%, day-gap→70%.
- **Bank-matched risk** (§4): `reconciled` field + `one_paid_one_outstanding` /
  `one_reconciled_one_outstanding` / `risk` in match_reasons; high-risk = `critical` severity.
- `duplicate_credit_note` type; void via `/CreditNotes/`.
- Message ends with "Can be two distinct documents, please check." when numbers differ.
