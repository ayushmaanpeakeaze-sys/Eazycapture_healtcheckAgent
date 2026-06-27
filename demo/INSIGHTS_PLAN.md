# Insights Module — Plan (Xenon Insight parity + AI edge)

Build a 9-KPI financial Insights module like Xenon Insight. This is a PLAN —
no engine/data code changed yet. Nothing here fetches from Xero until approved.

## Why it's feasible without a rebuild
We already have the data **pipe** (Nango → Xero proxy) and fetch transactions.
Insights need **report-level** data (P&L, Balance Sheet, Trial Balance) — the
**same pipe, new endpoints**. Pattern mirrors `fetch_all_invoices`:
add `fetch_profit_and_loss`, `fetch_balance_sheet`, `fetch_trial_balance`, etc.

## Architecture (3 layers)
```
1. FETCH    app/modules/integrations/  → new Nango proxy calls for Xero Reports
2. COMPUTE  app/services/insights/      → one calculator per KPI (pure, testable)
3. SERVE    /api/v1/insights/...        → cached snapshot the frontend renders
```
Reports are heavy + rate-limited, so **cache per company per day** (Xenon syncs
nightly). Store a computed snapshot; serve instantly; refresh on demand.

---

## Xero data sources to wire (all via Nango proxy)
| Endpoint | Gives | Powers KPIs |
|---|---|---|
| `Reports/ProfitAndLoss` (timeframe=MONTH, 3 yrs) | revenue, COGS, gross/net profit by month | 1, 4, 5 |
| `Reports/BalanceSheet` (date) | assets, liabilities, equity, retained earnings | 6, 7, 9 |
| `Reports/TrialBalance` (date) | every account balance | 2, 6, 8 |
| `Reports/BankSummary` | bank closing balances + movements | 2 |
| `Budgets` | budget figures | 1 |
| `Accounts` (already have) | identify DLA + bank accounts by type/code | 2, 6 |

> 80% of KPIs unlock from just **P&L + Balance Sheet + Trial Balance**.

---

## The 9 KPIs — definition, source, formula, status

### 1. Sales Tracker  🟢 partial now
- **Shows:** actual monthly sales vs budget / auto / manual target.
- **Source:** P&L revenue by month (or sum ACCREC — we have this); target from `Budgets`, or auto (prior-year +X%), or user-set.
- **Formula:** `actual[m]` vs `target[m]`; variance %.
- **Status:** actuals ✅ (have ACCREC) · budget ❌ (Budgets API).

### 2. Cash Health Check  🟡
- **Shows:** current cash/bank vs short/medium/long-term outgoings.
- **Source:** bank balances (TrialBalance / BankSummary); outgoings = unpaid bills (ACCPAY `amount_due`) we already have.
- **Formula:** `cash_now` vs bills bucketed by `due_date` (0–30 / 31–60 / 61+).
- **Status:** outgoings ✅ · live bank balance ❌.

### 3. Bookkeeping Health Check  ✅ DONE
- **Shows:** summary of our audit (health score + issue counts).
- **Source:** our engine — `/health/stats/` already returns it.
- **Status:** ✅ ship as-is.

### 4. Profitability  🟡  ← recommended first build
- **Shows:** monthly Sales Income, Gross Profit, Net Profit (chart).
- **Source:** `Reports/ProfitAndLoss?timeframe=MONTH&periods=12`.
- **Formula:** Gross = Sales − COGS; Net = Gross − Overheads (P&L gives these lines directly).
- **Status:** ❌ need P&L fetch (1 endpoint → this whole KPI).

### 5. Corporation Tax Estimate  🔴 (data easy, logic hard)
- **Shows:** CT liability current + previous 2 years.
- **Source:** P&L net profit per year (3× P&L pulls).
- **Formula:** taxable ≈ net profit ± add-backs; CT = taxable × rate. UK: 19% (≤£50k), 25% (≥£250k), marginal relief in between (FY2023+).
- **Status:** ❌ + needs **UK tax rules** (accountant sign-off).

### 6. Directors' Loan Accounts  🟠
- **Shows:** each director's loan balance; warn if overdrawn.
- **Source:** balance of the DLA nominal account(s) from `TrialBalance`; identify via account code/name ("Directors' Loan").
- **Formula:** overdrawn if director owes company (debit balance) → flag (s455 tax risk).
- **Status:** ❌ + need to map which account = DLA (config per client).

### 7. Business Valuation  🟡 (data easy, model choice)
- **Shows:** estimated value via chosen model.
- **Source:** P&L (profit) + Balance Sheet (net assets).
- **Formula:** pick one — sales × multiple, or (adj. profit/EBITDA) × multiple, or net assets (BS equity). User selects model + multiple.
- **Status:** ❌ + define default multiples.

### 8. Working Capital Cycle  🟡
- **Shows:** monthly cash-conversion ability.
- **Source:** debtors + creditors (we have AR/AP) + inventory (BS) + COGS/sales (P&L).
- **Formula:** `CCC = DSO + DIO − DPO` where DSO=debtors/sales×365, DIO=inventory/COGS×365, DPO=creditors/purchases×365.
- **Status:** 🟡 AR/AP ✅ · inventory + COGS ❌.

### 9. Dividend Availability  🟡
- **Shows:** distributable profit reserves available as dividend.
- **Source:** `Reports/BalanceSheet` retained earnings/equity.
- **Formula:** `distributable = retained_earnings + current_year_profit − dividends_already_declared`.
- **Status:** ❌ need Balance Sheet.

---

## Our differentiator (Xenon doesn't have this)
Xenon Insight = **numbers only**. We layer an **AI narrative** over the KPIs
(reuse `enrichment_service`/Groq):
> "Sales down 12% on last quarter and Hooli Inc's £500 bill is due — cash could
> tighten in 30 days; consider chasing INV-0006 (71 days overdue)."

KPIs + plain-English commentary = a real edge.

---

## Proposed endpoints
```
GET  /api/v1/insights/{company_id}/            → cached snapshot of all 9 KPIs + last_refreshed_at
POST /api/v1/insights/{company_id}/refresh     → Celery task: fetch reports → compute → store snapshot
GET  /api/v1/insights/{company_id}/{kpi}/      → single KPI detail (drill-down chart data)
```
Storage: new `insight_snapshot` table (company_id, kpi JSONB, computed_at) —
mirrors how `audit_batch` caches a run. Frontend renders widgets from the snapshot.

---

## Phasing (recommended order)
1. **Ship #3 now** (Bookkeeping Health) — already done, just surface in Insights.
2. **#4 Profitability** — wire **one** endpoint (`ProfitAndLoss`) end-to-end →
   first real proof, and the P&L fetch is reused by #1 and #5.
3. **#9 Dividend, #7 Valuation, #2 Cash, #8 Working Capital** — add Balance
   Sheet + Trial Balance fetch; these are mostly formulas after that.
4. **#1 Sales Tracker** — add Budgets (or user-set targets).
5. **#5 Corp Tax, #6 DLA** — last: need UK tax rules / per-client account mapping
   (accountant input).
6. **AI narrative layer** — once 2-3 KPIs exist, add commentary.

> One endpoint (`ProfitAndLoss`) unlocks 3 KPIs. Start there.

## Risks / decisions to confirm with sir
- **Tax rules (#5)** and **valuation model (#7)** need accountant sign-off on the
  exact formulas/rates.
- **DLA (#6)** needs per-client config: which nominal code is the director's loan.
- **Refresh cadence:** nightly (like Xenon) vs on-demand vs both. Reports are
  rate-limited, so cache aggressively.
