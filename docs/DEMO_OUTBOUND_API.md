# Demo Outbound API — Frontend Spec

Everything the frontend needs to render the Batch Inspector "Run demo" flow.
Returns a fixed set of 8 invoices with 13 deliberate issues. Cached, so it's
**instant (~3ms) and identical every call.**

---

## Endpoint

```
POST http://localhost:8001/api/v1/demo/run-outbound
```

- No request body
- No auth
- Response time: ~3ms (cached)

```js
const res = await fetch('http://localhost:8001/api/v1/demo/run-outbound', {
  method: 'POST',
});
const data = await res.json();
```

---

## Response shape

```json
{
  "status": "complete",

  "transactions": [          // ← render these as table rows
    {
      "transaction_id": "demo-001",
      "date": "2026-06-01",
      "description": "Adobe Creative Cloud subscription",
      "amount": "84.99",
      "vendor_name": "Adobe Systems",
      "tax_code": null,
      "current_account_code": "412",
      "invoice_number": "ADB-2026-001",
      "status": "DRAFT",
      "currency_code": "GBP",
      "type": "ACCPAY"
    }
    // ... 8 total
  ],

  "flagged": [               // ← flat list of all issues
    {
      "transaction_id": "demo-001",          // which row this belongs to
      "issue_type": "wrong_category",
      "severity": "medium",                  // critical | high | medium
      "message": "Adobe Creative Cloud — code 485 (Subscriptions), not 412 (Consulting & Accounting).",
      "current_code": "412",
      "suggested_code": "485",               // the fix (null if N/A)
      "suggested_name": "Subscriptions",
      "confidence": 0.96,                    // 0..1 (null for deterministic rules)
      "reasoning": "Subscription expense should use Subscriptions account.",
      "duplicate_of_transaction_id": null,   // set only for duplicate_* issues
      "duplicate_of_invoice_number": null,
      "duplicate_of_date": null,
      "this_is_likely_original": null
    }
    // ... 13 total
  ],

  "flags_by_txn": {          // ← SAME flags, pre-grouped by row (convenience)
    "demo-001": [ {…missing_tax…}, {…wrong_category…} ],
    "demo-002": [ {…future_dated…}, {…wrong_category…}, {…capital_item_review…} ]
    // ... only rows that have flags
  },

  "summary": {               // ← header stats
    "scanned": 8,            // total transactions
    "flagged_count": 13,     // total issues
    "flagged_rows": 7,       // rows with at least one issue
    "duplicate_groups": 1    // number of duplicate pairs
  }
}
```

---

## How to render

### 1. Header
```
Batch Inspector
{summary.scanned} transactions · {summary.duplicate_groups} duplicate groups
COMPLETE · Scanned {summary.scanned} · {summary.flagged_count} flagged
```

### 2. Table — one row per `transactions[]`
```
TXN ID | DATE | VENDOR | DESCRIPTION | AMOUNT | TAX CODE | FLAGS
```
For the FLAGS column, look up `flags_by_txn[transaction_id]` and render a
badge per issue:
```js
const flags = data.flags_by_txn[txn.transaction_id] || [];
flags.forEach(f => renderBadge(f.issue_type, f.severity));
```

### 3. Badge text (humanise `issue_type`)
| issue_type | Badge label | Colour by severity |
|---|---|---|
| `missing_tax` | MISSING TAX | critical = red |
| `missing_invoice_number` | MISSING INVOICE NUMBER | high = orange |
| `duplicate_bill` | DUPLICATE BILL | medium = yellow |
| `duplicate_invoice` | DUPLICATE INVOICE | |
| `wrong_category` | WRONG CATEGORY | |
| `capital_item_review` | CAPITAL ITEM | |
| `future_dated` | FUTURE DATED | |
| `sales_tax_on_bills` | SALES TAX ON BILL | |
| `purchase_tax_on_invoices` | PURCHASE TAX ON INVOICE | |
| `bill_or_direct_booking` | DIRECT BOOKING | |

### 4. Flag detail (on click / expand)
Show `message`. If `suggested_code` is present:
```
Current: {current_code} → Suggested: {suggested_code} ({suggested_name})
Confidence: {confidence * 100}%
```

### 5. Duplicate linking
For a `duplicate_bill` / `duplicate_invoice` flag:
- `this_is_likely_original: true` → "Keep this one"
- `this_is_likely_original: false` → "Void this one"
- `duplicate_of_transaction_id` → highlight/link the matching row

---

## The 13 demo flags (what the user will see)

| Row | Vendor | Flags |
|---|---|---|
| demo-001 | Adobe Systems | MISSING TAX (critical) · WRONG CATEGORY (412→485) |
| demo-002 | IKEA Business | FUTURE DATED · WRONG CATEGORY (461→740) · CAPITAL ITEM |
| demo-003 | Dell Technologies | WRONG CATEGORY (461→720) · CAPITAL ITEM |
| demo-004 | McKinsey & Company | DUPLICATE BILL (original) · SALES TAX ON BILL |
| demo-005 | McKinsey & Company | DUPLICATE BILL (void this) |
| demo-006 | Amazon Web Services | WRONG CATEGORY (412→485) |
| demo-007 | Acme Corp | PURCHASE TAX ON INVOICE |
| demo-008 | Landlord Holdings | DIRECT BOOKING (no invoice number) |

---

## TypeScript types

```typescript
interface DemoTransaction {
  transaction_id: string;
  date: string;
  description: string;
  amount: string;
  vendor_name: string;
  tax_code: string | null;
  current_account_code: string | null;
  invoice_number: string | null;
  status: string | null;
  currency_code: string;
  type: "ACCPAY" | "ACCREC" | "ACCPAYCREDIT" | "ACCRECCREDIT";
}

interface Flag {
  transaction_id: string;
  issue_type: string;
  severity: "critical" | "high" | "medium";
  message: string;
  current_code: string | null;
  suggested_code: string | null;
  suggested_name: string | null;
  confidence: number | null;
  reasoning: string | null;
  duplicate_of_transaction_id: string | null;
  duplicate_of_invoice_number: string | null;
  duplicate_of_date: string | null;
  this_is_likely_original: boolean | null;
}

interface DemoOutboundResponse {
  status: "complete";
  transactions: DemoTransaction[];
  flagged: Flag[];
  flags_by_txn: Record<string, Flag[]>;
  summary: {
    scanned: number;
    flagged_count: number;
    flagged_rows: number;
    duplicate_groups: number;
  };
}
```
