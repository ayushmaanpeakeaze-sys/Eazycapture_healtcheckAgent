# Frontend Integration — What's New (this build)

Everything the frontend needs to wire up from the latest backend work.
Base path for all routes: **`/api/v1/health`**. All routes are tenant-scoped —
pass the company via your existing auth (or `?company_id=<uuid>` in dev).

---

## 1. NEW action endpoints (the "buttons")

### Snooze / "Ignore for N days"
`POST /api/v1/health/trapped/{row_id}/snooze/`
```jsonc
// request
{ "days": 30, "reason": "review next month" }   // days 1..3650, default 30; reason optional
// response
{ "row_id": "<uuid>", "snoozed": true, "snoozed_until": "2026-07-16T12:00:00+00:00" }
```
Row disappears from the trapped feed now and **auto-reappears** after `snoozed_until`.

### Mark-OK / "Accept" (legit, not a false positive)
`POST /api/v1/health/trapped/{row_id}/mark-ok/`
```jsonc
{ "reason": "legit prepayment" }                 // response: { row_id, marked_ok: true }
```
Row drops from the feed permanently (distinct from **dismiss** = false positive).

### Bulk (apply one action to many rows)
`POST /api/v1/health/trapped/bulk/`
```jsonc
// request
{ "row_ids": ["<uuid>", "..."], "action": "dismiss" | "snooze" | "mark_ok",
  "days": 30, "reason": "batch cleanup" }        // 1..500 ids; days used only for snooze
// response
{ "action": "dismiss", "requested": 3, "succeeded": 3, "failed": 0,
  "results": [ { "row_id": "<uuid>", "ok": true, "error": null } ] }
```
Per-row result — one bad id never aborts the batch.

> Already existed (not new, but these are the other action buttons):
> `POST /trapped/{id}/dismiss/` (false positive) and
> `POST /trapped/{id}/resolve/` (write-back to Xero via `field_updates`).

---

## 2. CHANGED: Audit Configuration screen now has per-client settings

`GET /api/v1/health/audit-config/` and `PUT /api/v1/health/audit-config/`
now carry a **`settings`** object (the per-client tunable thresholds).

**GET response** adds:
```jsonc
{
  "disabled_rules": [...], "ignore_before": "2026-01-01",
  "settings": { "overdue_days": 30 },             // only the client's overrides
  "settings_defaults": { "overdue_days": 60, ... } // every default → render placeholders
}
```
**PUT body** accepts (all optional; unknown/bad keys are dropped server-side):
```jsonc
{ "disabled_rules": [...], "ignore_before": "2026-01-01",
  "settings": { "overdue_days": 30, "outlier_multiple": "2.5",
                "bank_exclude_accounts": ["091"] } }
```

### Settings keys (build a form input per row, placeholder = default)
| Key | Type | Default | Meaning |
|---|---|---|---|
| `duplicate_days_window` | int (days) | 7 | window for duplicate bill/invoice detection |
| `overdue_days` | int (days) | 60 | age before an unpaid invoice/bill is flagged |
| `credit_age_days` | int (days) | 60 | age before an unapplied credit note is flagged |
| `unapproved_grace_days` | int (days) | 7 | grace before a DRAFT/SUBMITTED doc is flagged |
| `inactive_days` | int (days) | 180 | inactive-contact message threshold |
| `supplier_min_txns` | int | 3 | min history for multi-account/tax supplier checks |
| `supplier_dominance` | float 0..1 | 0.70 | "usual" account/tax share before outliers flag |
| `outlier_min_txns` | int | 4 | min vendor history for amount-outlier baseline |
| `outlier_multiple` | decimal | 4.0 | flag when amount ≥ N× vendor median |
| `outlier_min_amount` | decimal | 100 | ignore outliers below this |
| `dup_contact_name_sim` | float 0..1 | 0.70 | min name similarity for duplicate contacts |
| `dup_contact_flag_threshold` | float 0..1 | 0.30 | duplicate-contact score threshold |
| `ignore_generic_contact` | bool | true | ignore shared info@/office-phone matches |
| `low_cost_asset_max` | decimal | 10000 | upper gate for low-cost-asset candidates |
| `capital_pre_filter_min` | decimal | 300 | lower gate for capital-review candidates |
| `misallocated_materiality` | decimal | 100 | min amount for a Misallocated-Item flag |
| `misallocated_vague_codes` | list[str] | [] | extra account codes treated as "vague" |
| `tax_missing_ignore_accounts` | list[str] | [] | accounts to skip in tax-missing checks |
| `tax_missing_ignore_contacts` | list[str] | [] | contacts (id or name) to skip in tax-missing |
| `multi_account_whitelist_contacts` | list[str] | [] | contacts allowed to use many accounts |
| `bank_balance_tolerance` | decimal | 0.01 | gap tolerance for the Bank Balance check |
| `bank_exclude_accounts` | list[str] | [] | bank accounts to skip (personal/credit-card) |
| `llm_min_confidence` | float 0..1 | 0.80 | min confidence for AI flags |

> Decimals come back as **strings** (e.g. `"2.5"`) to preserve precision; lists as arrays.

---

## 3. NEW issue type to render: `misallocated_item`

A trapped flag like the others. Fields: `issue_type: "misallocated_item"`,
`severity: "medium"`, `message`, `current_code` (the vague account). Suggested
display name: **"Misallocated Items"**.

---

## 4. CHANGED flag output: `unexpected_account`

When the org has contact defaults, this flag now runs **default-based** and
includes a suggested fix:
```jsonc
{ "issue_type": "unexpected_account", "current_code": "401",
  "suggested_code": "400", "suggested_name": "Rent",
  "message": "Acme usually posts to 400 (Rent); this used 401 (Travel)." }
```
Render `suggested_code`/`suggested_name` as the **"Change to"** option (Xenon-style).

---

## 5. NEW insight: Bank Balance (in the insights snapshot)

The per-org insights payload now has a **`bank_balance`** block (served by the
existing insights endpoint — no new route):
```jsonc
"bank_balance": {
  "accounts_checked": 2,
  "gap_count": 1,
  "gaps": [
    { "account_code": "090", "account_name": "Business Current",
      "statement_balance": 10000.0, "gl_balance": 9400.0,
      "gap": 600.0, "unreconciled_count": 3 }   // unreconciled_count = root-cause hint
  ]
}
```
Show one row per gap; `unreconciled_count` explains the likely cause.

---

## 6. NOT built yet (need a product decision before frontend wires them)

- **Void / Approve / Delete / Change-account** dedicated buttons — technically
  doable via the existing `POST /trapped/{id}/resolve/` (`field_updates`:
  `{"Status":"VOIDED"|"AUTHORISED"|"DELETED"}` or `{"AccountCode":"…"}`), which
  writes to real Xero when connected. Dedicated named endpoints await Kunal's
  sign-off (they mutate the client's live ledger).
- **Unexpected *Tax Code* (default-based)** — blocked: Xero exposes no
  contact-level default tax code. Still runs frequency-based for now.
