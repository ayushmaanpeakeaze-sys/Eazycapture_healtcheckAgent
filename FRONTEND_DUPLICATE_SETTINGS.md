# Frontend Integration — Duplicate Invoices: Settings + Display

Everything the frontend needs to wire up (1) the **per-check Settings
section** for Duplicate Invoices and (2) the **duplicate-match cards** in the
screenshot. All payloads below are **real responses from the running service**,
not mocks.

---

## 0. Base + auth (applies to every call)

| | |
|---|---|
| Base URL (dev) | `http://localhost:8001` |
| Prefix | `/api/v1/health` |
| Auth | `Authorization: Bearer <JWT>` — required unless the server runs with `AUTH_DISABLED=true` |
| Tenant | **Every** route takes `?company_id=<uuid>` as a query param |

A `401 {"detail":"Missing or malformed Authorization header."}` means the
bearer token is missing — it is **not** a bug in these endpoints.

---

## 1. Settings screen — read + save

The whole settings panel is **backend-driven**. Do not hardcode field labels,
help text, or which thresholds belong to which check — render from
`settings_schema`.

### 1a. GET `/api/v1/health/audit-config/?company_id=<uuid>`

Returns the catalog (on/off per check) + the per-check field metadata +
current values + defaults.

```jsonc
{
  "company_id": "2b66dabc-…",
  "total_checks": 34,
  "enabled_checks": 34,
  "disabled_rules": [],            // rule keys the user turned OFF
  "ignore_before": null,           // ISO date "YYYY-MM-DD" or null
  "settings": {},                  // this company's SAVED overrides (only changed keys)
  "settings_defaults": { "duplicate_days_window": 7, "duplicate_require_same_amount": true, … },
  "settings_schema": [ /* per-check field metadata — see below */ ],
  "groups": [ /* on/off catalog grouped by category — see 1c */ ]
}
```

**`settings_schema`** — array, one object per check. The Duplicate Invoices
entry (the only one populated today):

```jsonc
{
  "group": "Duplicates",
  "check": "duplicate_invoice",     // matches a rule key in `groups` → pair with its on/off toggle
  "fields": [
    { "key": "duplicate_days_window",            "label": "Date within",
      "type": "int",     "help": "Only pair invoices dated this close together.",
      "unit": "days", "min": 0, "max": 365, "step": 1, "default": 7 },
    { "key": "duplicate_require_exact_reference", "label": "Require exact reference",
      "type": "bool",    "help": "Both must share the same invoice reference (case-insensitive, exact).",
      "unit": null, "min": null, "max": null, "step": null, "default": false },
    { "key": "duplicate_require_same_amount",     "label": "Require same amount",
      "type": "bool",    "help": "Both must have the same total value.",
      "unit": null, "min": null, "max": null, "step": null, "default": true },
    { "key": "duplicate_also_check_paid",         "label": "Also check paid invoices",
      "type": "bool",    "help": "Include already-paid invoices in matching.",
      "unit": null, "min": null, "max": null, "step": null, "default": true },
    { "key": "duplicate_min_confidence",          "label": "Min confidence",
      "type": "percent", "help": "Hide matches scoring below this. Lower → more (looser) matches; higher → only the strongest.",
      "unit": null, "min": 0, "max": 1, "step": 0.05, "default": 0.6 }
  ]
}
```

**Render contract — `type` → control:**

| `type` | Control | Notes |
|---|---|---|
| `bool` | toggle | — |
| `int` | number input | show `unit` ("days"); clamp to `[min,max]`, increment `step` |
| `percent` | slider or % input | value is **0–1**; display `value*100`% (0.6 → "60%") |
| `amount` | money input | sent/stored as a **string** ("100.00") |
| `multiple` | "N×" number input | e.g. 4 → "4×" |
| `list` | tag / comma list | array of strings |

**Per-field value resolution:** current value = `settings[key]` if present,
else `field.default` (which equals `settings_defaults[key]`). Show the default
as a placeholder when unset.

### 1b. PUT `/api/v1/health/audit-config/?company_id=<uuid>`

Send back **only what changed**. Missing keys keep their defaults; unknown keys
and bad values are dropped server-side (never 500s on a bad threshold).

Request body:

```jsonc
{
  "disabled_rules": ["currency_mismatch"],   // checks turned OFF (rule keys); [] = all on
  "ignore_before": "2026-01-01",             // optional ISO date or null
  "settings": {                              // only the overridden threshold keys
    "duplicate_require_same_amount": false,
    "duplicate_days_window": 30,
    "duplicate_min_confidence": 0.5
  }
}
```

Response: **same shape as GET** (echoes the cleaned/persisted state) — use it to
re-hydrate the form.

Notes:
- `percent` fields go back as a number in `[0,1]` (0.5, not 50).
- `amount`/`multiple` go back as strings ("100.00", "2.5").
- Saving does **not** re-run the audit — see §3.

### 1c. `groups` (on/off catalog) — for the master toggle per check

```jsonc
"groups": [
  { "group": "Duplicates", "rules": [
      { "key": "duplicate_invoice", "label": "Duplicate invoices", "built": true, "enabled": true },
      { "key": "duplicate_bill",    "label": "Duplicate bills",    "built": true, "enabled": true },
      { "key": "duplicate_contact", "label": "Duplicate contacts", "built": true, "enabled": true }
  ]}, …
]
```

UI: for each check render the **master on/off** from `groups[].rules[].enabled`,
and underneath it the **threshold fields** from the matching
`settings_schema[].fields` (join on `check` === `rules[].key`). To turn a check
off, add its `key` to `disabled_rules` in the PUT. `built:false` = surfaced but
not emitted yet (toggle is a no-op) — render greyed/"coming soon".

---

## 2. Duplicate-match display (the screenshot)

The match cards come from the **trapped-invoices feed**. Each duplicate
produces **two rows** — the likely original and the likely duplicate — linked to
each other.

### 2a. GET `/api/v1/health/trapped-invoices/?company_id=<uuid>`

Query params: `limit` (1–200, default 50), `offset` (default 0),
`search` (document id), `include_dismissed` (**this is the "Show dismissed
matches" toggle**, default false). Frontend may poll every ~2s while an audit
runs.

```jsonc
{
  "results": [ TrappedInvoiceItem, … ],
  "total": 12, "limit": 50, "offset": 0
}
```

**`TrappedInvoiceItem`:**

```jsonc
{
  "id": "<uuid>",                 // row id → use in all /trapped/{row_id}/… actions
  "document_id": "<uuid>",        // the Xero invoice id
  "document_type": "ACCREC",
  "company_id": "<uuid>",
  "status": "blocked",
  "title": "INV-0001",
  "xero_url": "https://go.xero.com/…",   // "View" link
  "ai": { "explanation": "…", "severity_ai": "high", "confidence": 0.9 },  // "AI INSIGHT" banner (may be null)
  "result": { … }                 // the verdict JSONB — duplicate details live here ↓
}
```

**`result.flagged[]`** holds the issues. For a duplicate, the relevant entry
(real Hamilton Smith row):

```jsonc
{
  "issue_type": "duplicate_invoice",       // or "duplicate_bill"
  "severity": "high",
  "confidence": 0.97,
  "message": "Likely has duplicate Monthly Support (2026-03-22, USD 541.25; …). Likely the original — keep this; void Monthly Support.",
  "transaction_id": "<this row's tx id>",
  "duplicate_of_transaction_id": "<the partner tx id>",
  "duplicate_of_invoice_number": "INV-0005",
  "duplicate_of_date": "2026-03-22",
  "this_is_likely_original": true,         // true → "KEEP · ORIGINAL", false → "DUPLICATE"
  "match_reasons": {
    "tier": "high",                        // "high" | "medium" | "low"
    "confidence": 0.97,                    // 0.97 → "97%"
    "same_contact": true,                  // chip: "Same contact"
    "same_amount": true,                   // chip: "Same <currency><amount>"
    "amount": "541.25",
    "other_amount": null,                  // set only when amounts DIFFER (show both)
    "currency": "USD",
    "days_apart": 1,                       // chip: "1 day apart"
    "reference_match": "exact",            // "exact" | "none" | "different"
    "cross_contact": false,                // true → "across 2 contact records"
    "review": false                        // true → low-confidence "you decide" state
  }
}
```

**Chip mapping (screenshot → fields):**

| UI element | Source |
|---|---|
| `HIGH · 97%` | `match_reasons.tier` (badge) + `match_reasons.confidence × 100` |
| `Same contact` | `match_reasons.same_contact === true` |
| `Same US$541.25` | `same_amount` + `amount` + `currency` (if `!same_amount`, show `amount` vs `other_amount`) |
| `1 day apart` | `match_reasons.days_apart` |
| `🟢 Same reference` | `reference_match`: `exact` → green "Same reference"; `none` → grey "No reference"; `different` → amber "Different reference" |
| `KEEP · ORIGINAL` / `DUPLICATE` | `this_is_likely_original` (true/false) |
| partner invoice (`INV-0005`, date) | `duplicate_of_invoice_number`, `duplicate_of_date` |
| `Dismiss match` button | §2b dismiss |
| `View` | `xero_url` |
| `AI INSIGHT` banner | `ai.explanation` (or §2c `ai-insight`) |
| `Show dismissed matches` | re-fetch with `include_dismissed=true` |

**Pairing the two cards:** group rows where
`this.transaction_id === other.duplicate_of_transaction_id` (and vice-versa) —
render the `this_is_likely_original:true` row as ORIGINAL, the other as
DUPLICATE.

### 2b. Card actions (`row_id` = `TrappedInvoiceItem.id`)

| Button | Call | Body |
|---|---|---|
| **Dismiss match** (false positive) | `POST /trapped/{row_id}/dismiss/?company_id=` | `{ "dismissal_reason": "not a duplicate" }` |
| **Resolve / apply fix** | `POST /trapped/{row_id}/resolve/?company_id=` | `{ "field_updates": {…}, "resolution_notes": "voided dup" }` |
| **Void the duplicate** | `POST /trapped/{row_id}/void/?company_id=` | `{}` |
| **Snooze** | `POST /trapped/{row_id}/snooze/?company_id=` | per snooze schema |
| **Mark OK / keep both** | `POST /trapped/{row_id}/mark-ok/?company_id=` | `{}` |
| **Suggest fix (AI)** | `GET /trapped/{row_id}/suggest-fix/?company_id=` | — |
| **Apply AI fix** | `POST /trapped/{row_id}/apply-ai-fix/?company_id=` | — |
| **Bulk** | `POST /trapped/bulk/?company_id=` | list of row ids + action |

Dismiss response: `{ "row_id": "<uuid>", "dismissed": true }`. After any action,
re-fetch §2a (dismissed/resolved rows drop out unless `include_dismissed=true`).

### 2c. GET `/api/v1/health/trapped/{row_id}/ai-insight/?company_id=<uuid>`

On-demand "AI INSIGHT" copy if you don't want to rely on the inline `ai` blob.

---

## 3. Re-running after a settings change

Saving settings (§1b) does **not** recompute results — the trapped feed reflects
the **last audit**. To apply new settings:

1. `POST /api/v1/health/sync-xero-history/{company_id}/` → **202** `{ "batch_id": "<uuid>", … }`
2. Poll `GET /api/v1/health/sync-xero-history-status/{batch_id}/` until status is done
3. Re-fetch §2a `trapped-invoices`

So the **full settings→see-data loop** is: **PUT audit-config → POST
sync-xero-history → poll status → GET trapped-invoices.** (Verified on real
data: tightening/loosening the duplicate settings changes which pairs surface
and their tier/score.)

---

## 4. Suggested screen flow

1. On open: `GET audit-config` → render Duplicate Invoices section (master
   toggle from `groups`, fields from `settings_schema`, values from
   `settings`/defaults).
2. User edits → `PUT audit-config` with only changed keys.
3. Offer **"Re-run audit"** → §3 dispatch + poll.
4. Show results from `trapped-invoices`; cards from `result.flagged[].match_reasons`.
5. **Dismiss / Void / Keep** per card → re-fetch.

### Field reference (Duplicate Invoices) — defaults

| key | label | type | default |
|---|---|---|---|
| `duplicate_days_window` | Date within | int (days) | 7 |
| `duplicate_require_exact_reference` | Require exact reference | bool | false |
| `duplicate_require_same_amount` | Require same amount | bool | true |
| `duplicate_also_check_paid` | Also check paid invoices | bool | true |
| `duplicate_min_confidence` | Min confidence | percent (0–1) | 0.6 |
