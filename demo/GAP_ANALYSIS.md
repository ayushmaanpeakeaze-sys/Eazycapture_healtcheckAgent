# Gap Analysis — Our Engine vs Xenon Connect (reference)

This compares our health-check rule engine (`app/services/healthcheck/`) against
the documented behaviour of **Xenon Connect**, the reference product we benchmark
against. It is a planning document — **no engine logic has been changed.** Use it
to decide which checks to align and in what order.

Legend: ✅ aligned · ⚠️ threshold differs (easy) · ❌ definition/data gap (bigger)

---

## Summary table

| # | Check | Xenon definition | Our current logic | Status |
|---|-------|------------------|-------------------|--------|
| 1 | Old unpaid invoice / bill | ≥ 60 days old, outstanding | ≥ 60 days (`_OVERDUE_DAYS_THRESHOLD`) | ✅ |
| 2 | Low-cost fixed asset | Fixed-asset account + value below threshold | Fixed-asset account + amount < threshold (LLM) | ✅ |
| 3 | Multi-account supplier | Account vs contact's other txns (+3mo history) | Dominant ≥70%, ≥3 txns, flag the rest | ✅ ~same |
| 4 | Multi-tax-code supplier | Tax code vs contact's other txns | Dominant ≥70%, ≥3 txns, flag the rest | ✅ ~same |
| 5 | Duplicate invoices / bills | Default **1 day** apart; ref match + value match are **optional toggles**; ≥1 must be **unpaid** | **7 days**; ref **and** amount **both required**; paid included | ⚠️ |
| 6 | Unapproved invoice / bill | DRAFT/SUBMITTED, default age **0 days** | DRAFT/SUBMITTED, **> 7 days** (`_UNAPPROVED_AGE_DAYS`) | ⚠️ |
| 7 | Duplicate contacts | Name similarity **≥ 70%** | Strong identity (email/phone/bank/tax exact) + name **≥ 85%** | ⚠️ |
| 8 | Contact defaults | Sales/Purchase **account + tax code** (flag if any missing) | Only **account** (purchase for supplier / sales for customer); **tax code not checked** | ❌ |
| 9 | Unexpected account | Transaction's account ≠ contact's **default** account | **Frequency** outlier (account used once in a ≥100-doc batch) | ❌ |
| 10 | Unexpected tax code | Transaction's tax ≠ contact's **default** tax code | **Frequency** outlier (tax used once in a ≥100-doc batch) | ❌ |
| 11 | Bill or Direct Payment | **Bank payment** coded direct to account matched to an **unpaid bill**, same contact, ≤30 days | Proxy: authorised bill with **no bill number** | ❌ |
| 12 | Invoice or Direct Deposit | **Bank deposit** coded direct to account matched to an **unpaid invoice**, same contact, ≤30 days | Proxy: authorised invoice with **no invoice number** | ❌ |
| 13 | Capital item review | High value on **specific monitored accounts** (default 461 Printing & Stationery, 473 Repairs & Maintenance, + configurable) | LLM + high-value threshold on any expense | ❌ approach differs |

---

## ⚠️ Threshold differences (small — just config values)

### 5. Duplicate invoices / bills
- **Xenon defaults:** within **1 day**; "reference exactly same" and "value exactly same" are **opt-in toggles** (off by default); at least one document must be **unpaid** ("also check paid" is off by default).
- **Ours:** 7-day window, requires same normalized reference **and** same amount, and includes fully-paid documents.
- **Implication:** Xenon is stricter on date (1 vs 7 days) but looser on ref/value (those are optional). We deliberately widened the date window to 7 days to catch adjacent-day duplicates (the Hamilton Smith case) while excluding monthly recurring (~30 days). Our amount+ref requirement is intentionally precise to avoid false positives.
- **To align:** make the window, ref-match, value-match, and include-paid all **configurable** (per `audit_config`), defaulting close to Xenon but allowing our 7-day default. Constant: `_DUPLICATE_DAYS_WINDOW` in `deterministic.py`.

### 6. Unapproved invoices / bills
- **Xenon default age:** 0 days (flag as soon as DRAFT/SUBMITTED).
- **Ours:** `_UNAPPROVED_AGE_DAYS = 7`.
- **To align:** expose the age as a config value (keep 7 as our default, or set 0 to match Xenon).

### 7. Duplicate contacts
- **Xenon:** name similarity ≥ **70%** (configurable).
- **Ours:** strong identity signals (email/phone/bank/tax exact match → HIGH) **plus** fuzzy name ≥ **85%** (`_FUZZY_MIN`).
- **Note:** our strong-signal approach is arguably **more precise** than name-only matching — a shared bank account or tax number is far stronger evidence than a 70% name match. The only real gap is our name threshold (85%) is stricter than Xenon's (70%).
- **To align:** lower `_FUZZY_MIN` toward 70 and/or make it configurable; keep the strong-signal layer.

---

## ❌ Definition / data gaps (bigger — design work)

### 8. Contact defaults — missing tax-code check
- **Xenon** flags a contact if **any** of these 4 is missing: default sales account, default sales tax code, default purchases account, default purchases tax code.
- **Ours** only checks the **account** defaults (purchase for suppliers, sales for customers). We do **not** check default tax codes.
- **Fix:** in `contact_checks.py::_contact_defaults`, also check `PurchasesDefaultTaxType` / `SalesDefaultTaxType` (or Xero's equivalent field) and add them to the `missing` list. The snapshot/contact fetch must capture those fields.

### 9 & 10. Unexpected account / unexpected tax code — wrong definition
- **Xenon** = the transaction's account/tax code **differs from the contact's saved default** account/tax code (only fires when a default exists for that contact).
- **Ours** = a pure **frequency** outlier (an account/tax used only once in a batch dominated by another), gated to batches of ≥100 documents (`_FREQUENCY_MIN_BATCH`). On a normal-sized audit this rarely fires at all, and it doesn't reference contact defaults.
- **Why it matters:** these are fundamentally different checks. Xenon's version is far more useful and depends on **contact defaults** being populated (which is why Xenon's Contact Defaults check "powers" these two).
- **Fix:** rewrite `_find_unexpected_accounts` / `_find_unexpected_tax_codes` to compare each transaction's account/tax against the contact's default account/tax (fetched from Xero contacts), flagging only contacts that **have** a default set. Keep the old frequency check as a separate, optional signal if desired. Depends on capturing contact default account/tax in the data layer.

### 11 & 12. Bill or Direct Payment / Invoice or Direct Deposit — needs bank data
- **Xenon** matches a **bank transaction** (money out / money in) coded directly to a nominal account against an **open (unpaid) bill / invoice** for the **same contact**, within a configurable window (default 30 days). It is a cross-source reconciliation check.
- **Ours** is an in-batch **proxy**: an authorised bill/invoice with no number is treated as a "possible direct booking." The code comment already acknowledges this:
  > "The richer 'Invoice/Bill or Direct' check (matching a bank RECEIVE/SPEND line to an open invoice/bill) needs bank-transaction data; this is the in-batch proxy until that data is wired in."
- **Fix (largest):** start fetching **bank transactions** from Xero (`BankTransactions` endpoint — type SPEND/RECEIVE, the account they were coded to). Then, per contact, match a direct-coded bank payment/deposit to an open bill/invoice within the date window. This is new data plumbing (a `BankTransaction` model/snapshot table) + a new matching rule. It also unlocks a proper reconciliation table for the SQL demo (sir's `bank_transactions` query).

### 13. Capital item review — account-targeted vs threshold
- **Xenon** monitors **specific expense accounts** by default (461 Printing & Stationery, 473 Repairs & Maintenance) plus any the firm adds, and flags high-value postings there as possibly capital.
- **Ours** is LLM-driven over any expense above a pre-filter (`_CAPITAL_PRE_FILTER_MIN = 300`, max 10000) and decides capitalise/expense holistically.
- **Note:** ours is broader (any account, AI judgement) but doesn't specifically target the accounts Xenon watches. Consider seeding the LLM with the 461/473 hint, or adding a deterministic "high value on a watched account" pre-filter.

---

## Suggested order of work (when we revisit the engine)

1. **Quick wins (config):** #5, #6, #7 — expose thresholds in `audit_config`, default close to Xenon. Low risk.
2. **Contact defaults tax code (#8):** small, needs one extra field in the contact fetch.
3. **Unexpected account/tax redefinition (#9, #10):** medium — depends on capturing contact default account/tax; then rewrite the two rules to compare-vs-default.
4. **Capital review targeting (#13):** small — add 461/473 (and config) as a deterministic pre-filter / LLM hint.
5. **Bank-transaction reconciliation (#11, #12):** largest — new data source (Xero BankTransactions), new model/snapshot table, new matching rule. Also unlocks the bank-reconciliation SQL demo.

> Reminder: nothing above is implemented yet. This is the plan; the live engine is unchanged.
