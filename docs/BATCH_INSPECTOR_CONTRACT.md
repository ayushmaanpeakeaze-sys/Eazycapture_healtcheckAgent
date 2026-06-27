# Batch Inspector — Integration Contract

The AI-agent's pre-ledger firewall. EazyCapture sends a batch of documents
**before** publishing them to Xero; the firewall returns flagged issues per
document. EazyCapture then decides what to publish.

**This backend only inspects — it never writes to Xero for batches.**
Publishing is EazyCapture's responsibility.

---

## Endpoint

```
POST http://<ai-agent-host>:8001/api/v1/health-check/batch
Content-Type: application/json
```

No auth required when `JWT_SECRET` is unset or `AUTH_DISABLED=true`
(POC). In production, send the same bearer token used across services.

---

## Request body

```json
{
  "transactions": [
    {
      "transaction_id": "tx-001",        // REQUIRED — any unique id (EazyCapture's own)
      "date": "2026-05-12",              // REQUIRED — YYYY-MM-DD
      "description": "AWS production",    // REQUIRED — min 1 char
      "amount": "2480.55",               // REQUIRED — string decimal
      "vendor_name": "Amazon Web Services", // REQUIRED
      "type": "ACCPAY",                  // REQUIRED — ACCPAY|ACCREC|ACCPAYCREDIT|ACCRECCREDIT
      "tax_code": "INPUT",               // optional — null if missing
      "current_account_code": "485",     // optional — the account it will post to
      "invoice_number": "AWS-001",       // optional — null if missing
      "due_date": "2026-06-12",          // optional
      "status": "DRAFT",                 // optional — DRAFT|AUTHORISED|PAID|...
      "amount_paid": null,               // optional
      "amount_due": null,                // optional
      "currency_code": "GBP",            // optional — defaults GBP
      "posted_date": null                // optional
    }
  ],
  "context": {                           // OPTIONAL — strongly recommended
    "chart_of_accounts": [
      {"code": "485", "name": "Subscriptions", "type": "EXPENSE"},
      {"code": "720", "name": "Computer Equipment", "type": "FIXEDASSET"}
    ],
    "tax_rates": [
      {"code": "INPUT", "name": "Tax on Purchases", "rate": "20.0"},
      {"code": "OUTPUT", "name": "Tax on Sales", "rate": "20.0"}
    ],
    "base_currency": "GBP"
  }
}
```

### Why send `context`?

Without it, the engine falls back to generic UK fixtures. **With** the
company's real Chart of Accounts, the `wrong_category` and
`wrong_direction_account` checks suggest the **correct** account code from
the company's own COA — not a generic guess. Always send it if you have it.

---

## Response body

```json
{
  "flagged": [
    {
      "transaction_id": "tx-001",        // maps back to your input row
      "issue_type": "wrong_category",    // see issue types below
      "severity": "high",                // critical|high|medium
      "message": "AWS — code 485 (Subscriptions), not 412.",
      "current_code": "412",             // optional
      "suggested_code": "485",           // optional — the fix
      "suggested_name": "Subscriptions", // optional
      "confidence": 0.95,                // optional, 0..1
      "duplicate_of_transaction_id": "tx-002",  // duplicate_* only
      "duplicate_of_invoice_number": "INV-002", // duplicate_* only
      "this_is_likely_original": false          // duplicate_* only
    }
  ]
}
```

- One input row can produce **multiple** flags (e.g. missing tax + duplicate).
- Map flags to rows by `transaction_id`.
- A row with **no flags** is clean — safe to publish.

---

## Issue types

**High importance**
`duplicate_invoice`, `duplicate_bill`, `old_unpaid_invoice`, `old_unpaid_bill`,
`old_unsettled_sales_credit`, `old_unsettled_purchase_credit`,
`opening_balance_difference`

**Medium**
`invoice_or_direct_booking`, `bill_or_direct_booking`, `low_cost_fixed_asset`,
`capital_item_review`, `wrong_category`, `multi_account_supplier`,
`multi_tax_code_supplier`, `unexpected_account`, `unexpected_tax_code`,
`purchase_tax_missing`, `sales_tax_missing`, `sales_tax_on_bills`,
`purchase_tax_on_invoices`, `unapproved_invoice`, `unapproved_bill`

**Supporting**
`missing_tax`, `missing_vendor`, `missing_invoice_number`,
`wrong_direction_account`, `invalid_tax_code`, `future_dated`,
`invalid_status_combo`

---

## EazyCapture-side pseudocode

```python
import httpx

async def inspect_then_publish(documents, company_coa, company_tax_rates):
    # 1. Inspect the whole batch BEFORE touching Xero
    resp = await httpx.AsyncClient().post(
        "http://ai-agent:8001/api/v1/health-check/batch",
        json={
            "transactions": documents,
            "context": {
                "chart_of_accounts": company_coa,
                "tax_rates": company_tax_rates,
                "base_currency": "GBP",
            },
        },
        timeout=600,
    )
    flagged = resp.json()["flagged"]

    # 2. Split clean vs flagged
    flagged_ids = {f["transaction_id"] for f in flagged}
    clean = [d for d in documents if d["transaction_id"] not in flagged_ids]
    needs_review = [d for d in documents if d["transaction_id"] in flagged_ids]

    # 3. EazyCapture publishes the clean ones to Xero (its own Nango calls)
    await publish_to_xero(clean)

    # 4. Surface the flagged ones to the user to fix, then re-inspect
    return {"published": len(clean), "needs_review": needs_review, "flags": flagged}
```

---

## Performance notes

- Batches up to ~200 transactions process in one shot.
- `wrong_category` / `capital_item_review` use the LLM — expect ~30-90s for
  a 60-document batch on the free Groq tier. Deterministic rules
  (duplicates, missing tax, etc.) are instant.
- Use `POST /api/v1/health-check/batch/async` + `GET /api/v1/audit/progress/{batch_id}`
  (SSE) if you want a progress bar for large batches.

---

## Test it now

```bash
curl -X POST http://localhost:8001/api/v1/demo/run-outbound | jq '.flagged | length'
# Returns flagged count for 8 built-in demo invoices
```
