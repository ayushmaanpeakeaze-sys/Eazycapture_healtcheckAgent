# DB-Backed Sync Architecture — Design Doc

Status: **Proposal** (no code yet). Author hand-off for review.

Goal: move the bookkeeping checks from "full live Xero fetch on every audit"
to a **Xenon-style synced model** — entities persisted in org-scoped tables,
kept fresh by an incremental sync, checks run over the DB. This is a
production-scale concern (5k invoices / 2k contacts / 15k bank txns), not an
MVP one.

---

## 1. Current state (code-verified, not assumed)

| Layer | Today | File |
|---|---|---|
| Issues / results | ✅ DB-backed. Dashboard reads `health_check_result`, not live. | `services/trapped_service.py` |
| Org scoping | ✅ `company_id` on every table; every query `WHERE company_id=?`. | `core/multi_tenant.py` |
| Audit history | ✅ `audit_batch` per run. | `models.py` |
| **Entity fetch** | ❌ **Full LIVE Xero pull every audit** (`_pull_xero_documents`). No persistence for connected orgs. | `tasks.py:383` |
| Incremental sync | ❌ None. No `If-Modified-Since`, no watermark. | — |
| `invoice` / `invoice_line_item` | ⚠️ Modeled + used **only as the seed source** for non-connected orgs (`tasks.py:450`). Not populated from live Xero. | `models.py:98` |
| `snap_*` tables | 🗑️ Legacy. Stale data, **zero code references**. Drop them. | — |

**Conclusion:** the *results* layer is already Xenon-style. The gap is the
*entity* layer — it re-fetches everything from Xero on each audit.

---

## 2. Goals / non-goals

**Goals**
- Persist Accounts, Contacts, Invoices+Bills (+lines), Bank Transactions (+lines),
  Credit Notes, Tax Rates in org-scoped tables.
- First connect → full sync; thereafter → incremental (`If-Modified-Since`).
- Checks read from DB (`WHERE company_id=?`), not from a live pull.
- Keep the existing `BatchTransaction` shape so **check logic doesn't change** —
  only the *source* of the rows changes.
- Reuse existing batch-progress infra for "Importing… 45%".

**Non-goals (for this phase)**
- Real-time webhooks (Xero push). Polling/incremental is enough to start.
- Changing any check's detection logic.
- Nango Sync deploy (deferred — see §8 for the trade-off).

---

## 3. Target architecture

```
First connect
  User connects Xero ─▶ pick org ─▶ FULL SYNC (paginate all entities)
        ─▶ upsert into org tables ─▶ set per-entity watermark
        ─▶ run all checks over DB ─▶ store issues ─▶ dashboard

Steady state (Celery beat, hourly/daily)
  INCREMENTAL SYNC (If-Modified-Since = watermark)
        ─▶ upsert only changed/new ─▶ advance watermark
        ─▶ re-run checks over DB ─▶ upsert issues
```

The audit (`historical_audit_task`) stops calling `_pull_xero_documents` and
instead **reads the synced tables**. A new `sync_xero_task` owns all Xero I/O.

---

## 4. Data model

### 4.1 Sync watermark — `sync_state`
One row per (company, entity). Drives incremental fetch + observability.

```
sync_state
  id                uuid pk
  company_id        uuid  fk company(id) on delete cascade
  entity            text  -- 'invoices' | 'contacts' | 'bank_transactions'
                          --  'accounts' | 'tax_rates' | 'credit_notes'
  last_synced_at    timestamptz null   -- wall clock of last successful sync
  watermark_utc     timestamptz null   -- max UpdatedDateUTC seen → next If-Modified-Since
  full_sync_done    boolean default false
  last_status       text   -- 'ok' | 'running' | 'error'
  last_error        text null
  records_synced    int default 0
  unique (company_id, entity)
```

### 4.2 Extend existing `invoice` / `invoice_line_item`
Already has the business fields. Add **sync keys**:

```
invoice  (ADD)
  xero_id            text       -- Xero InvoiceID (upsert key)
  contact_id         text       -- Xero Contact​ID (checks need this)
  updated_date_utc   timestamptz-- Xero UpdatedDateUTC (watermark source)
  reconciled         boolean default false
  + unique (company_id, xero_id)
  + index (company_id, contact_id)

invoice_line_item (ADD)
  xero_line_item_id  text null  -- Xero LineItemID (stable upsert within an invoice)
```

### 4.3 New entity tables (all org-scoped, all upsert on `(company_id, xero_id)`)

```
contact
  id uuid pk | company_id fk | xero_id text | name text | first_name | last_name
  email text | tax_number text | phone text
  is_customer bool | is_supplier bool | status text   -- ACTIVE | ARCHIVED
  sales_account text | purchase_account text | sales_tax text | purchase_tax text
  updated_date_utc timestamptz
  unique (company_id, xero_id) | index (company_id, lower(name))

account                              -- chart of accounts
  id | company_id | xero_id | code text | name text
  type text  -- FIXED | EXPENSE | REVENUE | BANK | …
  tax_type text | status text | updated_date_utc
  unique (company_id, code)

tax_rate
  id | company_id | tax_type text | name text | rate numeric | status text
  unique (company_id, tax_type)

bank_transaction                     -- Money In / Money Out
  id | company_id | xero_id | type text  -- RECEIVE | SPEND
  contact_id text | bank_account_code text | reference text
  date date | total numeric(12,2) | currency_code text
  status text | is_reconciled bool | updated_date_utc
  unique (company_id, xero_id) | index (company_id, contact_id)

bank_transaction_line
  id | bank_transaction_id fk | xero_line_item_id | description
  account_code text | tax_type text | line_amount numeric(12,2)

credit_note  (+ credit_note_line, credit_note_allocation)
  id | company_id | xero_id | type  -- ACCRECCREDIT | ACCPAYCREDIT
  contact_id | number text | status text | total | remaining_credit numeric
  date date | updated_date_utc
  unique (company_id, xero_id)
```

> Multi-org is **already** solved by `company_id` + the `WHERE company_id=?`
> discipline in `multi_tenant.py`. Every new table follows the same rule.

---

## 5. Sync flow

### 5.1 First run (full)
```
for entity in [accounts, tax_rates, contacts, invoices, bank_transactions, credit_notes]:
    page = 1
    while True:
        rows = proxy_get(endpoint, params={page})       # 100/page
        if not rows: break
        upsert(rows, company_id)                         # ON CONFLICT (company_id, xero_id)
        advance watermark = max(watermark, max(UpdatedDateUTC in rows))
        page += 1
    sync_state[entity] = {full_sync_done: true, watermark_utc, last_synced_at: now}
```
- Order matters: accounts + tax_rates + contacts first (checks join against them),
  then documents.
- Emit progress to the existing Redis batch-meta (`stage_label="Importing contacts… "`,
  percent = entities_done / total) → frontend's existing `/api/v1/audit/progress/{batch_id}`.

### 5.2 Incremental (steady state)
```
for entity:
    headers = {"If-Modified-Since": sync_state[entity].watermark_utc}   # RFC1123 UTC
    paginate as above, upsert, advance watermark
```
- Xero returns only records with `UpdatedDateUTC >= If-Modified-Since`
  (**includes** VOIDED/DELETED status changes → we upsert the new status, so a
  voided invoice is reflected, not silently kept).
- Subtract a small safety overlap (e.g. watermark − 60s) to avoid edge-loss on
  same-second updates; upsert is idempotent so re-seeing a row is harmless.

### 5.3 Scheduling
- Celery beat: `sync_xero_task(company_id)` hourly (configurable per plan).
- After a successful sync → enqueue `historical_audit_task` (re-run checks).
- A manual "Sync now" button maps to the same task.

---

## 6. Rewire points (small, localized)

| Change | Where |
|---|---|
| New `sync_xero_task` (all Xero I/O + upserts) | `modules/healthcheck/tasks.py` (new) |
| `_fetch_audit_transactions` reads DB instead of `_pull_xero_documents` | `tasks.py:368` — swap the `use_nango` branch to query `invoice`/`bank_transaction` tables and reshape via the **existing** `_reshape_invoice` |
| Context (accounts/tax/contacts/defaults) built from DB | the audit already builds `context` from fetched COA/contacts — point it at the synced `account`/`contact`/`tax_rate` tables |
| `coding_options` / `coding-defaults` read DB | `services/contact_defaults_service.py` (optional — can stay live, it's small) |
| Drop `snap_*` + their migration | new alembic migration |

Checks themselves: **no change**. They already take `list[BatchTransaction]` +
a context; we just build those from DB rows.

---

## 7. Check → table mapping (sanity that DB has enough)

| Check | Reads |
|---|---|
| Duplicate invoices / bills | `invoice` (+lines) |
| Duplicate contacts | `contact` |
| Old unpaid invoice / bill | `invoice` (status, amount_due, due_date) |
| Unapproved invoice / bill | `invoice` (status DRAFT/SUBMITTED) |
| Low-cost fixed asset / Capital item | `invoice` + `bank_transaction` lines × `account.type` |
| Unexpected account / tax | `invoice` + `bank_transaction` × `contact` defaults |
| Bill/Invoice or direct settlement | `invoice` × `bank_transaction` |
| Inactive contacts | `contact` × last activity from `invoice`/`bank_transaction` |
| Tax-missing, multi-account/tax supplier, misallocated | `invoice` (+lines) × `account` |

All covered by the tables in §4. ✅

### 7.1 Execution: SQL-pushdown vs Python (don't over-promise "all SQL")
Once data is in the DB, **most** checks become fast indexed SQL — the DB returns
only matching rows instead of Python looping over 5k invoices. But a few are
**algorithmic** and can't be pure SQL; for those, SQL narrows the candidate set
and Python does the scoring.

**Pure SQL (filter / join / aggregate — index-friendly):**
old-unpaid invoice/bill · unapproved invoice/bill · old sales/purchase credits ·
low-cost fixed asset · capital item · misallocated · sales/purchase tax-missing ·
sales-tax-on-bills · purchase-tax-on-invoices · multi-account/tax supplier
(`GROUP BY contact`) · contact-defaults · inactive-contacts (aggregate) ·
unexpected account/tax (`JOIN contact WHERE used_account <> default_account` —
the check is pure default-comparison) · bill/invoice direct settlement (self-join
on contact + amount + date-window).

**Needs Python (SQL only pre-filters):**
- **Duplicate invoices/bills** — fuzzy scoring waterfall (same-ref→1.0,
  diff-amount, recurring-cadence detection, days-apart, confidence ≥0.90). SQL
  narrows to same-contact / near-amount candidates; Python scores.
- **Duplicate contacts** — name *similarity* (token blend + legal-suffix strip +
  generic-word down-weight). `GROUP BY LOWER(name)` catches only EXACT dupes and
  misses "Ronny" vs "Ronny agency" (94%) etc. Keep the Python similarity;
  optionally a `pg_trgm` GIN index narrows candidates first.

**Migration is two levels — ship Level 1 first:**
1. **Level 1 (this doc's §2 promise):** read DB → build `BatchTransaction` →
   existing Python checks **unchanged**. The fetch win (incremental) lands
   immediately, zero per-check rewrite, safe.
2. **Level 2 (opportunistic):** rewrite the pure-SQL checks as queries where it
   pays; leave the fuzzy ones in Python with a SQL candidate pre-filter. Not a
   prerequisite — a later optimization.

Suggested indexes: `(company_id,status)`, `(company_id,due_date)`,
`(company_id,contact_id)`, `invoice_line_item(account_code)`,
`GIN(contact.name gin_trgm_ops)`.

---

## 8. Source mechanism — DECISION (phased): Actions+DB now, Sync later

> **DB-backed architecture ≠ Nango Sync.** What §4–§7 describe is a **mini sync
> engine we own** — `read watermark → fetch changes (If-Modified-Since) → upsert
> DB → advance watermark → run checks`. It is *technically* a sync, just not
> Nango's. That's the whole point: we get the DB benefits (persistence,
> incremental, history) **without** Nango Sync's costs (opaque cursor +
> corruption, record-storage cost, delete-engine, cache dependency, deploy).
> `sync_state` lives in our DB — inspect it in SQL, and a bad watermark
> self-heals with a full idempotent re-pull.

The real axis is **stored checks** (which we already do — `health_check_result`
+ `audit_batch`), not "Sync vs Action". Decision:

- **NOW (startup, 50–100 orgs): fetch → DB → check on DB.**
  - Reads: a Celery job calls the **custom Action** (`list-invoices-full`, …) —
    OR the proxy; for reads they're equivalent (same `/Invoices` + line items).
    Incremental via a simple per-entity `modified_since` we own.
  - Writes: **Actions** (clear win — approve / delete / create-credit-note /
    update-contact / recode-line; queueable, retryable, MCP-exposable).
  - This sidesteps Sync's worst failure modes: no opaque Nango cursor to
    corrupt (a bad watermark = just do a full idempotent re-pull to self-heal),
    deletes handled by a periodic full-reconcile sweep we control.
- **LATER (10k–100k invoices/org): migrate reads to custom Nango Sync** (Nango's
  incremental engine, records storage, webhooks). The `invoices-full` action +
  sync are both already scaffolded → migration is cheap.

> The scalability lever is **DB persistence + incremental fetch**, *not*
> Action-vs-Proxy. Both transports return full line items; pick Action for a
> consistent deployed-function story (and the Sync on-ramp), or proxy for
> zero-infra. Writes always go through Actions.

### Sync demerits we are explicitly deferring (why "later", not "now")
complexity (cursor/watermark/deletes/retries/pagination state) · eventual
consistency (hourly = up to 1 h stale) · storage+indexing cost at 100s of orgs ·
watermark-corruption bugs · delete/tombstone handling · schema churn when Xero
adds fields. None worth it below ~10k invoices/org.

### Why CUSTOM (not pre-built) syncs — verified live on this connection
| Pre-built sync | What it returns | Verdict |
|---|---|---|
| `accounts` | Code, Name, Type, TaxType — full | ✅ use as-is |
| `credit-notes` | full incl. LineItems | ✅ use as-is |
| `invoices` | header only — **no LineItems, no Reference, no HasAttachments** | ❌ custom needed |
| `bank-transactions` | **no line items** (only `lineItemCount` + bank account) | ❌ custom needed |
| `contacts` | **only `{id, name}`** — no defaults/email/status | ❌ custom needed |
| `general-ledger` | would give line-level account/tax per posting… | ⚠️ blocked — connection lacks `accounting.journals.read`; only posted txns, no drafts/defaults |

So: pre-built `accounts` + `credit-notes` are fine; **`invoices`,
`bank-transactions`, `contacts` need custom syncs** with the full model.

### Custom syncs use the SAME endpoints the proxy proved
`/Invoices` and `/BankTransactions` return full line items (verified). A custom
sync wraps exactly that (+ pagination + `If-Modified-Since` + `batchSave`) — so
**no `journals.read` scope, no reconnect, no GL workaround.** The
`general-ledger` route is unnecessary.

### LATER only — if we move reads to Nango Sync, syncs must be CUSTOM
(For the NOW path we just call `/Invoices` etc. directly — these tables only
matter if/when we adopt Nango's Sync engine at scale.)

| Sync | Adds over pre-built | Endpoint |
|---|---|---|
| `invoices-full` | LineItems, Reference, HasAttachments | `/Invoices` |
| `contacts-full` | defaults (sales/purchase acct+tax), email, status | `/Contacts` |
| `bank-transactions-full` | line items, isReconciled | `/BankTransactions` |
| `credit-notes` (pre-built) | — | — |
| `accounts` (pre-built) | — | — |

`invoices-full.ts` scaffolded in `nango-integrations/xero/syncs/` and
**verified-compilable** via the Functions API (`createSync` + incremental
`If-Modified-Since` + `batchSave`) — the on-ramp is ready, but **not used in the
NOW path.**

### Actions (writes)
| Action | Drives |
|---|---|
| `approve-invoice` (→ AUTHORISED) | Unapproved invoices/bills |
| `delete-invoice` (→ DELETED) | Unapproved (created in error) |
| `create-credit-note` (+ allocate) | resolve old-unpaid (already built on proxy) |
| `update-contact` | Contact Defaults fix |
| `recode-line` (update invoice/bank line account) | low-cost / capital / misallocated "Save Changes" |

### Ingestion
- **NOW (mini sync engine):** the Celery `sync_xero_task` calls `/Invoices` etc.
  (proxy or Action) page-wise and upserts **directly** into the §4 tables. No
  Nango cache, no `GET /records`, **no `nango deploy`.** Then
  `historical_audit_task` runs checks over the DB — unchanged.
- **LATER (Nango Sync):** Nango populates its record cache; we pull via
  `GET /records?model=Invoice` and upsert. Adds webhooks + Nango-managed
  incremental, at the cost of deploy + cache dependency.

### Trade-off (NOW path)
Almost none beyond writing the entity tables + one incremental task. No Nango
deploy, no cache, no cursor engine. In return: stateful, incremental, fewer Xero
calls, no rate-limit risk, historical tracking — the production posture, owned
by us and debuggable in SQL.

---

## 9. Xero specifics / gotchas

- **Pagination**: `page` param, 100/page. Invoices, BankTransactions, Contacts,
  CreditNotes page. Accounts + TaxRates don't (small → single call).
- **If-Modified-Since**: header, RFC1123 UTC. Filters by `UpdatedDateUTC`.
- **Tenant header**: every proxy call needs `xero-tenant-id` (already handled in
  our proxy layer).
- **Rate limits**: Xero ≈ 60 calls/min + 5,000/day per org; concurrency 5.
  Full sync of 5k invoices = ~50 calls → well within limits. Throttle to ≤5
  concurrent, backoff on 429.
- **Deletes/voids**: surfaced via `If-Modified-Since` as status changes → upsert.
  (Hard-deletes in Xero are rare for these types; a periodic full reconcile can
  catch any drift.)
- **`updated_date_utc` is the single source of truth** for the watermark — store
  it on every row.

---

## 10. Concurrency & integrity
- One sync per company at a time (advisory lock on `company_id` or a
  `sync_state.last_status='running'` guard).
- Audit must not run mid-sync → chain: sync → on success → audit.
- Upserts are idempotent (`ON CONFLICT (company_id, xero_id) DO UPDATE`), so a
  retried/overlapping sync never duplicates.

---

## 11. Rollout phases
1. **Migrations**: add `sync_state`, extend `invoice`/`invoice_line_item`, add
   `contact`/`account`/`tax_rate`/`bank_transaction(+line)`/`credit_note(+…)`;
   drop `snap_*`.
2. **Sync task** (full + incremental) + beat schedule + progress emit.
3. **Rewire** `_fetch_audit_transactions` + context builders to read DB.
4. **Backfill**: run a one-time full sync per connected company.
5. **Flip**: feature-flag `AUDIT_SOURCE=db|live`; default `live` until verified,
   then `db`.
6. Delete the live-pull path once DB path is proven.

## 12. Effort (rough)
- Migrations + models: ~0.5–1 day
- Sync task (full + incremental + progress): ~1.5–2 days
- Rewire audit to DB + context: ~1 day
- Tests (sync upsert, incremental, watermark, parity with live): ~1 day
- **Total ≈ 4–5 focused days** for full Phase 1.

## 13. Open decisions
- Sync cadence per plan (hourly vs nightly)?
- Hard-delete reconciliation: periodic full sweep (weekly) vs ignore?
- Keep `coding_options` live (always-fresh dropdown) or DB-backed?
- Feature-flag rollout vs hard cut?

---

### TL;DR
Results layer is already DB-backed + org-scoped. Decision (phased): **NOW —
fetch (custom Action or proxy) → upsert §4 tables with a simple `modified_since`
incremental → run checks over DB; writes via Actions. LATER (10k+ invoices/org)
— migrate reads to custom Nango Sync.** The lever is DB+incremental, not
Action-vs-Proxy (both return full line items). Pre-built `accounts` +
`credit-notes` are fine as-is; `invoices`/`contacts`/`bank` need full data
(custom Action now, custom Sync later — both scaffolded). Checks don't change.
Effort now ≈ entity tables + one ingest job + point `_fetch_audit_transactions`
at the DB (~2–3 days); full Sync engine deferred.
