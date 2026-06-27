# Frontend Integration — Insights KPIs

Everything the frontend needs to build the Insights dashboard. All responses
are typed (Pydantic) so the shapes below are stable.

## Basics
- **Base URL:** `http://localhost:8001` (prod: your API host)
- **Auth:** `Authorization: Bearer <token>` (same token as the rest of the app)
- **Multi-tenant:** every route is scoped to `{company_id}`; the user only sees
  companies they're allowed to (403/404 otherwise).
- **Live data:** these fetch the report live from Xero on each call (~1–3s).
  Show a loading state. (Caching is a planned optimisation — see Notes.)
- **Errors:** `409` = company not connected to Xero · `502` = Xero
  unavailable/rate-limited · `404` = unknown company. All return
  `{"detail": "..."}`.

---

## Endpoints — 3 snapshot routes (+ Bookkeeping Health)

All 9 KPIs are served from a **pre-computed snapshot** (refreshed nightly + on
demand). There are only **3 routes** — no per-KPI live endpoints.

| Use | Endpoint |
|---|---|
| **Firm overview (panorama)** | `GET /api/v1/insights/firm-summary/` |
| **One client — all 9 KPIs** | `GET /api/v1/insights/{company_id}/` |
| **Refresh one client** | `POST /api/v1/insights/{company_id}/refresh/` |
| Bookkeeping Health (existing) | `GET /api/v1/health/stats/?company_id={id}` |

> **Sections 1–5 below describe the SHAPE of each KPI inside the snapshot
> `payload`** (returned by `GET /{company_id}/`). They are reference for the
> data shapes — **not** separate endpoints. Ignore any per-KPI URLs shown there.

---

## ⭐ Recommended loading pattern (snapshot-based — fast + scalable)

The per-KPI endpoints below fetch **live from Xero** (slow, rate-limited). For
production use the **snapshot** routes instead — they serve pre-computed data
from the DB (milliseconds), refreshed nightly + on a manual button.

| Use | Endpoint |
|---|---|
| **Firm overview (panorama)** — all clients rolled up | `GET /api/v1/insights/firm-summary/` |
| **One client — all 9 KPIs in one call (fast)** | `GET /api/v1/insights/{company_id}/` |
| **"Refresh" button** — recompute one client now | `POST /api/v1/insights/{company_id}/refresh/` |

### A. Firm overview / Panorama  →  `GET /api/v1/insights/firm-summary/`
No `company_id` — returns a roll-up across every client the logged-in user can see.
```json
{
  "totals": {                       // firm-level counts → the tiles
    "total_clients": 12,
    "with_snapshot": 11,
    "in_profit": 8,
    "in_loss": 3,
    "cash_tight": 4,                // coverage_ratio < 0.2
    "working_capital_negative": 5,
    "dla_overdrawn": 2,
    "unreconciled_bank_items": 47
  },
  "clients": [                      // per-client rows → the table
    {"company_id": "1a55c9dc-...", "name": "Demo Company (Global)",
     "computed_at": "2026-06-15T02:31:00Z", "net_profit": 5550.34,
     "working_capital": -2921.06, "cash_coverage": 0.14, "dla_overdrawn": false,
     "unreconciled_bank_items": 8, "last_bank_reconciled": "2026-04-30",
     "most_recent_transaction": "2026-05-22"}
  ]
}
```
**Render — what to show and how:**

h*Top summary tiles* (from `totals.*`):
| Tile | Field | Colour / emphasis |
|---|---|---|
| Total clients | `total_clients` | neutral |
| In profit | `in_profit` | green |
| In loss | `in_loss` | red if > 0 |
| Cash tight | `cash_tight` | amber (coverage < 0.2) |
| Working capital −ve | `working_capital_negative` | amber |
| DLA overdrawn | `dla_overdrawn` | red (tax risk) |
| Unreconciled bank items | `unreconciled_bank_items` | amber (total to reconcile) |
| Data coverage | `with_snapshot` / `total_clients` | "11 / 12 synced" |

*Clients table* (one row per `clients[]` item):
| Column | Field | How to render |
|---|---|---|
| Client | `name` | link → opens that client's Insights (`GET /{company_id}/`) |
| Net profit | `net_profit` | green ≥ 0, red < 0; `null` → "—" |
| Working capital | `working_capital` | red if < 0 |
| Cash coverage | `cash_coverage` | red/amber if < 0.2 (×100 → show as %) |
| DLA | `dla_overdrawn` | red badge "Overdrawn" if true |
| Unreconciled bank | `unreconciled_bank_items` | amber if high; count |
| Last reconciled | `last_bank_reconciled` | date / "—" |
| Most recent txn | `most_recent_transaction` | relative time (freshness) |
| Updated | `computed_at` | relative time ("2h ago") |

*Behaviour:*
- **Sortable / filterable** table (e.g. sort by lowest cash coverage to triage).
- A row with `net_profit: null` (or all-null) = **no snapshot yet** (client not
  connected, or not computed) → show "—" and a muted row.
- **Row click → that client's full Insights** (`GET /{company_id}/`).
- This is the firm's "triage" screen: red/amber tiles tell the accountant which
  clients need attention first.

### B. One client, all KPIs  →  `GET /api/v1/insights/{company_id}/`
```json
{
  "company_id": "1a55c9dc-...",
  "computed_at": "2026-06-15T02:31:00Z",
  "status": "ok",
  "stale": false,
  "payload": {
    "profitability":       { ... same shape as §1 ... },
    "sales_tracker":       { ... same shape as §2 ... },
    "financial_position":  { ... same shape as §3: cash_health, working_capital, dividend, valuation, position ... },
    "corporation_tax":     { ... same shape as §4 ... },
    "directors_loans":     { ... same shape as §5 ... },
    "bookkeeping_health":  { "health_score": 58, "open_issues": 48,
                             "audited_documents": 62, "audited_contacts": 51,
                             "last_audit_at": "2026-06-10T10:13:22Z" },
    "bank_reconciliation": { "total_transactions": 22, "unreconciled_count": 8,
                             "unreconciled_value": 516.30,
                             "last_reconciled_date": "2026-04-30",
                             "most_recent_transaction": "2026-05-22" }
  }
}
```
**Render:** ONE call → hydrate ALL widgets from `payload`. `stale:true` (or
`status:"none"`) → snapshot missing/old, show a "Refresh" prompt. `computed_at`
= "last updated" label.

### C. Refresh button  →  `POST /api/v1/insights/{company_id}/refresh/`
Returns `202 {"company_id": "...", "status": "queued"}`. Recompute runs in the
background (~few seconds). Poll `GET /{company_id}/` until `computed_at` updates.

> **Sections 1–5 below are payload-shape reference only** — that data comes
> inside `GET /{company_id}/` → `payload`. There are no separate per-KPI
> endpoints (any URLs in those sections are superseded by the snapshot route).

---

## 1. Profitability  →  monthly bar/line chart
`GET /api/v1/insights/{company_id}/profitability/?periods=11`
- `periods` (optional, 1–23): prior months to compare. Default 11 (= 12 columns).

```json
{
  "company_id": "1a55c9dc-...",
  "kpi": "profitability",
  "report_name": "Profit and Loss",
  "report_date": "12 June 2026",
  "periods": ["30 Jun 26", "30 May 26", "30 Apr 26", "30 Mar 26"],
  "series": {
    "sales":        [0.0, 2075.68, 10710.89, 10694.69],
    "gross_profit": [0.0, 1299.70, 10710.89, 10694.69],
    "net_profit":   [0.0, -3271.57, 2075.59, 6746.32]
  },
  "totals": { "sales": 23481.26, "gross_profit": 22705.28, "net_profit": 5550.34 }
}
```
**Render:** `periods` = X-axis; plot 3 series (Sales / Gross / Net). `totals` =
summary tiles. Net can be negative (loss month) — handle red bars.

---

## 2. Sales Tracker  →  actual vs target
`GET /api/v1/insights/{company_id}/sales-tracker/?periods=11&target=8000`
- `target` (optional): manual monthly target. Omit → auto target (avg of active months).

```json
{
  "company_id": "1a55c9dc-...",
  "kpi": "sales_tracker",
  "periods": ["30 Jun 26", "30 May 26", "30 Apr 26", "30 Mar 26"],
  "actual": [0.0, 2075.68, 10710.89, 10694.69],
  "target": 7827.09,
  "target_basis": "auto (avg of active months)",
  "total_sales": 23481.26,
  "rows": [
    {"period": "30 Apr 26", "actual": 10710.89, "target": 7827.09,
     "variance": 2883.80, "variance_pct": 36.8, "met_target": true}
  ]
}
```
**Render:** bars = `actual`, line = `target`. Use `rows[].met_target` for
green/red, `variance_pct` for the ± label. Show `target_basis` as a small note.

---

## 3. Financial Position  →  5 KPIs in ONE call
`GET /api/v1/insights/{company_id}/financial-position/`
(One Balance Sheet fetch powers all five — call once, render 5 widgets.)

```json
{
  "company_id": "1a55c9dc-...",
  "kpi": "financial_position",
  "report_date": "12 June 2026",
  "position": {
    "total_assets": 16203.33, "total_liabilities": 12665.57, "net_assets": 3537.76,
    "cash": 1760.54, "current_assets": 9744.51, "fixed_assets": 4698.28,
    "current_liabilities": 12665.57
  },
  "cash_health": {
    "cash": 1760.54, "current_liabilities": 12665.57,
    "coverage_ratio": 0.14, "shortfall": 10905.03
  },
  "working_capital": {
    "current_assets": 9744.51, "current_liabilities": 12665.57,
    "working_capital": -2921.06, "current_ratio": 0.77, "healthy": false
  },
  "dividend": {
    "retained_earnings": 1462.17, "current_year_earnings": 2075.59,
    "distributable_reserves": 3537.76,
    "basis": "retained earnings + current-year earnings"
  },
  "valuation": { "model": "net_asset", "net_asset_value": 3537.76 }
}
```

**How to render each sub-block:**
| Widget | Use these fields |
|---|---|
| **Cash Health** | `cash_health.coverage_ratio` (gauge); red if `shortfall > 0` |
| **Working Capital** | `working_capital.working_capital` + `current_ratio`; red if `healthy:false` |
| **Dividend Availability** | `dividend.distributable_reserves` (big number); show `basis` as footnote |
| **Business Valuation** | `valuation.net_asset_value` + `model` label |
| **Financial Position** | `position.*` — assets / liabilities / net-assets summary |

---

## 4. Corporation Tax  →  estimate tile
`GET /api/v1/insights/{company_id}/corporation-tax/`

```json
{
  "company_id": "1a55c9dc-...",
  "kpi": "corporation_tax",
  "period_basis": "trailing 12 months",
  "taxable_profit": 5550.34,
  "tax_estimate": 1054.56,
  "band": "small profits rate (19%)",
  "effective_rate": 19.0,
  "note": "Estimate before tax adjustments ... Confirm with accountant."
}
```
**Render:** big number = `tax_estimate`; subtitle = `band` + `effective_rate`%.
Always show `note` as a small disclaimer (it's an estimate). `period_basis`
tells the user the window.

---

## 5. Directors' Loan Accounts  →  list / warning
`GET /api/v1/insights/{company_id}/directors-loans/`

```json
{
  "company_id": "1a55c9dc-...",
  "kpi": "directors_loans",
  "detected": true,
  "accounts": [
    {"account": "Director's Loan Account", "code": "835", "balance": 12000.0,
     "overdrawn": true, "note": "Overdrawn — director owes the company (possible s455 tax)."}
  ],
  "note": "Auto-detected by account name — confirm the mapping per client."
}
```
**Render:** if `detected:false` → show "No DLA mapped" + a button to map an
account manually. If `detected:true` → list each account; **red warning when
`overdrawn:true`** (director owes the company — tax risk). Show `note`.

---

## 6. Bookkeeping Health
**Now included in the snapshot** → `payload.bookkeeping_health`:
`{ health_score, open_issues, audited_documents, audited_contacts, last_audit_at }`.
Render the health-score widget from there — **no extra call needed**.

For the full issue breakdown (`open_document_issues`, `open_contact_issues`,
`by_issue_type[]`), the existing `GET /api/v1/health/stats/?company_id={id}` is
still available (live audit data).

---

## 7. Bank Reconciliation  →  `payload.bank_reconciliation`
```json
{
  "total_transactions": 22,
  "unreconciled_count": 8,
  "unreconciled_value": 516.30,
  "last_reconciled_date": "2026-04-30",
  "most_recent_transaction": "2026-05-22"
}
```
**Render:**
- **Unreconciled Bank Items** = `unreconciled_count` (big number) + `unreconciled_value` (£) underneath; amber if > 0.
- **Last Bank Reconciled** = `last_reconciled_date` (relative time; red/amber if old).
- **Most Recent Transaction** = `most_recent_transaction` (freshness — "is the book up to date?").

**Important — what this is (and isn't):** these come from Xero's `BankTransactions`
`IsReconciled` flag. `unreconciled_count` = bank transactions entered in Xero that
aren't reconciled. Xero's API does **not** expose raw bank-statement feed lines,
so this is the closest available signal (same as competitors). No "reconcile"
action is possible via API — display only.

---

## ⚠️ Two KPIs are first-pass (being confirmed with the accountant)
Both work and return data, but the logic is provisional:
| KPI | What's provisional |
|---|---|
| **Corporation Tax** | Uses trailing-12-months net profit, **before tax adjustments** (depreciation add-backs, capital allowances, losses) and current-year UK rates. Prior 2 years not included yet. Always show the `note` disclaimer. |
| **Directors' Loans** | Auto-detects accounts named "director"/"DLA". Person-named loans need manual mapping (`detected:false`). A per-client account-mapping setting is coming. |

Render them, but keep the disclaimers visible.

---

## Notes for the frontend
- **Loading:** each call hits Xero live (~1–3s). Show a spinner / skeleton.
- **Refresh:** a manual "Refresh" button per widget is fine; avoid auto-polling
  (Xero rate-limits at 60 calls/min/org).
- **Currency:** figures are in the org's base currency (GBP for the demo).
- **Empty months:** P&L can return `0.0` for months with no activity — that's
  valid, not an error.
- **One call, many widgets:** `financial-position` returns 5 KPIs — fetch once,
  hydrate all five cards from the single response.
