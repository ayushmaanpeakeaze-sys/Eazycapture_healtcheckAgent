# Frontend API Reference — Response Bodies (real captures)

Everything we built/discussed via the screenshots, with **actual** response
bodies captured from the live Demo org. Base path: **`/api/v1/health`**.
All routes are tenant-scoped — pass auth (or `?company_id=<uuid>` in dev).
Decimals come back as **strings**; dates ISO-8601.

---

## 1. Trapped feed — the data source for Duplicate Invoices, Old Unpaid, etc.

`GET /api/v1/health/trapped-invoices/?company_id=<uuid>&limit=50&offset=0`

```jsonc
{
  "results": [ TrappedInvoiceItem, ... ],
  "total": 49,
  "limit": 50,
  "offset": 0
}
```

### A real Duplicate-Invoice row (one `TrappedInvoiceItem`)
```jsonc
{
  "id": "523be515-4401-473f-af26-d493b50a0862",        // ← use for actions
  "document_id": "cb5119d0-9759-49d3-800b-7d0a90818178",
  "document_type": "ACCREC",                            // ACCREC=invoice, ACCPAY=bill
  "status": "blocked",
  "xero_url": "https://go.xero.com/...",                // ← "View" button
  "result": {
    "rule_ids": ["duplicate_invoice"],                  // ← filter the tab on this
    "messages": "Likely has duplicate Monthly Support (2026-03-22, USD 541.25 ...",
    "vendor_name": "Hamilton Smith Ltd",                // Contact column
    "invoice_number": "INV-0001",                       // Invoice Ref column
    "reference": "Monthly Support",                     // Details column
    "amount": "541.25",                                 // Total Value column
    "currency_code": "USD",
    "due_date": "2026-04-01",
    "invoice_status": "PAID",                           // Paid? → status === "PAID"
    "flagged": [
      {
        "issue_type": "duplicate_invoice",
        "severity": "high",
        "confidence": 0.97,
        "message": "Likely has duplicate Monthly Support ... void Monthly Support.",
        "this_is_likely_original": true,                // which row to KEEP
        "duplicate_of_transaction_id": "b7e0c5f4-9f52-4126-b102-45fd12eaa3ca",
        "duplicate_of_invoice_number": "INV-0005",      // the matched partner
        "duplicate_of_date": "2026-03-22",
        "current_code": null, "suggested_code": null, "suggested_name": null,
        "match_reasons": {                              // ← "what matched" chips
          "same_contact": true,
          "same_amount": true, "amount": "541.25", "currency": "USD",
          "days_apart": 0,                             // 0 = same date
          "reference_match": "exact",                  // exact | normalized | none
          "cross_contact": false,
          "confidence": 0.97, "tier": "high"
        }
      }
    ]
  }
}
```
**Render notes**
- **Pair the two rows** via `flagged[].duplicate_of_transaction_id`; `this_is_likely_original` marks the keep-vs-duplicate.
- **Paid?** = `result.invoice_status === "PAID"` (or `amount_due === 0` once we add it).
- **"What matched" chips** → `flagged[].match_reasons`: e.g. `Same customer ✓ · Same amount £541.25 ✓ · Same date ✓ · Same reference ✓ · High confidence`. (`reference_match`: exact/normalized → "Same reference"; none → "No reference"; `days_apart: 0` → "Same date", else "N days apart"; `tier` → High/Medium badge.)

> Same shape powers every check tab — filter `result.rule_ids` (duplicate_bill,
> old_unpaid_invoice, unapproved_invoice, multi_account_supplier, anomaly, etc.).

---

## 2. Action buttons (per row + bulk)

| Button | Request | Response |
|---|---|---|
| **Dismiss** (false positive) | `POST /trapped/{id}/dismiss/` `{ "dismissal_reason": "..." }` | `{ "row_id": "...", "dismissed": true }` |
| **Snooze / Ignore-N-days** | `POST /trapped/{id}/snooze/` `{ "days": 30, "reason": "..." }` | `{ "row_id":"...", "snoozed": true, "snoozed_until": "2026-07-16T12:00:00+00:00" }` |
| **Mark-OK** (accept legit) | `POST /trapped/{id}/mark-ok/` `{ "reason": "..." }` | `{ "row_id": "...", "marked_ok": true }` |
| **Void / Approve / Delete / Change-account** | `POST /trapped/{id}/resolve/` `{ "field_updates": {"Status":"VOIDED"} }` | see below |
| **Bulk** | `POST /trapped/bulk/` `{ "row_ids":[...], "action":"dismiss"|"snooze"|"mark_ok", "days":30, "reason":"..." }` | see below |

**Resolve (Void/Approve/Delete/Change-account)** — real Xero write when connected:
```jsonc
{ "row_id":"...", "document_id":"...", "resolved": true,
  "applied_updates": {"Status":"VOIDED"}, "skipped_fields": [],
  "xero_url":"https://go.xero.com/...", "xero_response": { "stub": false, ... },
  "error_code": null, "error_detail": null }
```
- Void → `{"Status":"VOIDED"}` · Approve → `{"Status":"AUTHORISED"}` · Delete → `{"Status":"DELETED"}` · Change-account → `{"AccountCode":"400"}`
- Only show **Void** when not paid (`invoice_status !== "PAID"`).

**Bulk** response:
```jsonc
{ "action": "dismiss", "requested": 3, "succeeded": 3, "failed": 0,
  "results": [ { "row_id": "...", "ok": true, "error": null } ] }
```
After any action the row drops from the feed → re-fetch §1 (snoozed rows auto-return after `snoozed_until`).

---

## 3. Contact Defaults screen  (REAL capture)

`GET /api/v1/health/contact-defaults/?company_id=<uuid>&missing_only=true&search=`
`missing_only=false` = the **"Show all Xero contacts"** toggle.

```jsonc
{
  "connected": true,
  "total": 29,
  "missing_count": 29,
  "contacts": [
    {
      "contact_id": "aacecb74-ef1e-44e0-ba52-0bc521639697",
      "name": "PC Complete",
      "is_customer": false,
      "is_supplier": true,
      "current_defaults": {                  // pre-fill the dropdowns
        "sales_account": "",
        "sales_tax": "",
        "purchases_account": "",
        "purchases_tax": ""
      },
      "missing": ["purchases_account", "purchases_tax"]   // highlight these
    }
    // ...
  ],
  "accounts": [                              // "Usual ... Account" dropdown options
    { "code": "090", "name": "Business Bank Account", "type": "BANK" },
    { "code": "200", "name": "Sales", "type": "REVENUE" }
  ],
  "tax_rates": [                             // "Usual ... Tax Code" dropdown options
    { "code": "CAN030", "name": "Exempt Sales" },
    { "code": "OUTPUT2", "name": "20% (VAT on Income)" }
  ]
}
```
> `connected: false` (with empty arrays) when the org has no Nango connection.

**Confirm** (write chosen defaults → Xero):
`POST /api/v1/health/contact-defaults/{contact_id}/confirm/`
```jsonc
// request — any subset of the four; only provided fields are written
{ "sales_account": "200", "sales_tax": "OUTPUT2",
  "purchases_account": "400", "purchases_tax": "INPUT2" }
// response
{ "contact_id": "...", "ok": true,
  "applied": { "SalesDefaultAccountCode":"200", "AccountsReceivableTaxType":"OUTPUT2",
               "PurchasesDefaultAccountCode":"400", "AccountsPayableTaxType":"INPUT2" },
  "error": null }
```

**Bulk confirm**:
`POST /api/v1/health/contact-defaults/bulk-confirm/`
```jsonc
// request
{ "items": [ { "contact_id":"...", "defaults": { "purchases_account":"400" } }, ... ] }
// response
{ "requested": 2, "succeeded": 2, "failed": 0,
  "results": [ { "contact_id":"...", "ok": true, "applied": {...}, "error": null } ] }
```
- **View** → `xero_url` (or `https://go.xero.com/Contacts/View/{contact_id}`).
- **Dismiss / bulk-dismiss** → reuse §2 (`/trapped/{id}/dismiss/`, `/trapped/bulk/`) — contact-defaults flags are trapped rows too (`document_type: "CONTACT"`).

Field ↔ Xero mapping: `sales_account→SalesDefaultAccountCode`, `sales_tax→AccountsReceivableTaxType`, `purchases_account→PurchasesDefaultAccountCode`, `purchases_tax→AccountsPayableTaxType`.

---

## 4. Audit Configuration screen (per-client settings)

`GET` / `PUT /api/v1/health/audit-config/`
```jsonc
{
  "company_id": "...",
  "total_checks": 32,
  "enabled_checks": 31,
  "disabled_rules": ["anomaly"],
  "ignore_before": "2026-01-01",
  "settings": { "overdue_days": 30 },                 // client's overrides only
  "settings_defaults": {                              // all defaults → placeholders
    "duplicate_days_window": 7, "overdue_days": 60, "credit_age_days": 60,
    "unapproved_grace_days": 7, "inactive_days": 180, "supplier_min_txns": 3,
    "supplier_dominance": 0.7, "outlier_min_txns": 4, "outlier_multiple": "4.0",
    "outlier_min_amount": "100", "dup_contact_name_sim": 0.7,
    "dup_contact_flag_threshold": 0.3, "ignore_generic_contact": true,
    "low_cost_asset_max": "10000", "capital_pre_filter_min": "300",
    "misallocated_materiality": "100", "misallocated_vague_codes": [],
    "tax_missing_ignore_accounts": [], "tax_missing_ignore_contacts": [],
    "multi_account_whitelist_contacts": [], "bank_balance_tolerance": "0.01",
    "bank_exclude_accounts": [], "llm_min_confidence": 0.8
  },
  "groups": [ /* rule catalog grouped, each {key,label,built,enabled} */ ]
}
```
`PUT` body: `{ "disabled_rules":[...], "ignore_before":"YYYY-MM-DD", "settings": { "overdue_days": 30 } }` — unknown/bad keys dropped server-side.

---

## 5. Bank Balance (in the per-org insights payload)

Served by the insights snapshot endpoint under `payload.bank_balance` (no new route).
Validated live: joins BankSummary (statement) ↔ TrialBalance (GL) on accountID.
```jsonc
"bank_balance": {
  "accounts_checked": 2,
  "gap_count": 1,
  "gaps": [
    { "account_code": "090", "account_name": "Business Bank Account",
      "statement_balance": 10000.0, "gl_balance": 9400.0,
      "gap": 600.0, "unreconciled_count": 3 }   // root-cause hint
  ]
}
```
(Demo org currently: `accounts_checked: 1, gap_count: 0` — its one bank account reconciles, £1760.54 = £1760.54.)

---

## 6. Left-menu counts + "what matched" (status)

- **Counts** ("Duplicate Invoices (2)") → `GET /api/v1/health/stats/` (per-issue-type + severity counts).
- **"What matched" chips** for duplicates → ✅ **built** — `flagged[].match_reasons` (see §1). Carries same_contact / same_amount+amount / days_apart / reference_match / cross_contact / confidence / tier.
- **Re-run audit** ("Reanalyse") → audit-dispatch `POST`.
- **Per-row AI insight** ("Gareth says") → `GET /trapped/{id}/ai-insight/`.

---

## New issue type to render
`misallocated_item` (severity medium; `current_code` = the vague account) — display "Misallocated Items".
`unexpected_account` / `unexpected_tax_code` now carry `suggested_code` (+`suggested_name`) = the contact's default → render as the "Change to" option.
