# Health Checks — Master Index (vs Xenon)

One page: every check, its build status, and a link to its spec. Status verified
against the live engine (`app/services/healthcheck/`).

Legend: ✅ built (~85-90%) · 🟡 partial · ❌ not built · 🔓 unblocked (bank data now wired)

| # | Check | Status | Spec |
|---|-------|--------|------|
| 1 | Duplicate Contacts | ✅ (stronger than Xenon) | [doc](CHECK_DUPLICATE_CONTACTS.md) |
| 2 | Duplicate Invoices | ✅ | [doc](CHECK_DUPLICATE_INVOICES.md) |
| 3 | Duplicate Bills | ✅ | [doc](CHECK_DUPLICATE_BILLS.md) |
| 4 | Old Unpaid Invoices | ✅ | [doc](CHECK_OLD_UNPAID_INVOICES.md) |
| 5 | Old Unpaid Bills | ✅ | [doc](CHECK_OLD_UNPAID_BILLS.md) |
| 6 | Old Sales Credits | ✅ | [doc](CHECK_OLD_SALES_CREDITS.md) |
| 7 | Old Purchase Credits | ✅ | [doc](CHECK_OLD_PURCHASE_CREDITS.md) |
| 8 | Unapproved Invoices | ✅ | [doc](CHECK_UNAPPROVED_INVOICES.md) |
| 9 | Unapproved Bills | ✅ | [doc](CHECK_UNAPPROVED_BILLS.md) |
| 10 | Wrong-Direction Account | ✅ | (in [GAP_ANALYSIS](GAP_ANALYSIS.md)) |
| 11 | Sales Tax on Bills | ✅ | [doc](CHECK_SALES_TAX_ON_BILLS.md) |
| 12 | Purchase Tax on Invoices | ✅ | [doc](CHECK_PURCHASE_TAX_ON_INVOICES.md) |
| 13 | Wrong Category (AI) | ✅ | (in [DOC_VS_REALITY](DOC_VS_REALITY.md)) |
| 14 | Capital Item Review (AI) | ✅ | [doc](CHECK_CAPITAL_ITEM_REVIEW.md) |
| 15 | Low-Cost Fixed Assets (AI) | ✅ | [doc](CHECK_LOW_COST_FIXED_ASSET.md) |
| 16 | Amount Outlier | ✅ | (in [DOC_VS_REALITY](DOC_VS_REALITY.md)) |
| 17 | Data-integrity (paid/overpaid/future/missing) | ✅ | (in [DOC_VS_REALITY](DOC_VS_REALITY.md)) |
| 18 | Opening Balance Differences | 🟡 ~70% (Xero-only; Xenon = Companies House) | [doc](CHECK_OPENING_BALANCE.md) |
| 19 | Multi-Account Suppliers | 🟡 ~80% (no 3-mo lookback) | [doc](CHECK_MULTI_ACCOUNT_SUPPLIERS.md) |
| 20 | Multi-Tax Code Suppliers | 🟡 ~80% | [doc](CHECK_MULTI_TAX_SUPPLIERS.md) |
| 21 | Sales Tax Missing | 🟡 ~55% (no org-VAT gate/filters) | [doc](CHECK_SALES_TAX_MISSING.md) |
| 22 | Purchase Tax Missing | 🟡 ~55% (no ignore-list) | [doc](CHECK_PURCHASE_TAX_MISSING.md) |
| 23 | Contact Defaults | ✅ all 4 defaults (sales/purchase × account/tax) + live list (show-all) + Confirm write-back to Xero + bulk | [doc](CHECK_CONTACT_DEFAULTS.md) |
| 24 | Inactive Contacts | 🟡 ~45% (name-match, not last-txn date) | [doc](CHECK_INACTIVE_CONTACTS.md) |
| 25 | Unexpected Account Used | ✅ default-based (Xenon parity; frequency fallback) | [doc](CHECK_UNEXPECTED_ACCOUNT.md) |
| 26 | Unexpected Tax Code Used | ✅ default-based (Xenon parity; uses contact `AccountsReceivableTaxType`/`AccountsPayableTaxType`; frequency fallback) | [doc](CHECK_UNEXPECTED_TAX.md) |
| 27 | Bill or Direct Payment | 🟡 ~30% proxy · 🔓 | [doc](CHECK_BILL_OR_DIRECT.md) |
| 28 | Invoice or Direct Deposit | 🟡 ~30% proxy · 🔓 | [doc](CHECK_INVOICE_OR_DIRECT.md) |
| 29 | Misallocated Items | ✅ detection built (deterministic; vague-account + materiality, per-client) | [doc](CHECK_MISALLOCATED_ITEMS.md) |
| 30 | Bank Balance Check | ✅ detection built (statement vs GL gap per account + unreconciled root-cause; per-client tolerance + exclude-list) | [doc](CHECK_BANK_BALANCE.md) |
| 31 | Unreconciled Bank Items | 🟡 summary built; per-account ❌ | [doc](CHECK_UNRECONCILED_BANK.md) |
| 32 | Unprocessed Bank Items | 🟡 summary built; per-account ❌ | [doc](CHECK_UNPROCESSED_BANK.md) |

## Headline counts
- ✅ **Built (~85-90%):** 22 (incl. Misallocated, default-based Unexpected Account **+ Tax**, Bank Balance, Contact Defaults + write-back)
- 🟡 **Partial:** 7
- ❌ **Not built (but buildable):** 0
- 🔒 **Blocked:** none (earlier "no contact default tax" was WRONG — Xero exposes `AccountsReceivableTaxType`/`AccountsPayableTaxType`; Unexpected-Tax default-based is now just pending work)
- ⚙️ **Cross-cutting #1 (per-client configurable settings): DONE** · **#2 (action buttons): local-state set DONE** — see roadmap below

## The one permanent limit
**Raw bank statement feed lines** — Xero does **not** expose them via API
(official). Xenon **screen-scrapes** via a browser extension. So the *true*
unprocessed/unreconciled **feed** is off-limits to everyone's API path; we (and
Xenon's API) use the `IsReconciled` count instead.

## Cross-cutting roadmap (applies to many checks)
1. ✅ **Per-client configurable settings — DONE.** `audit_config['settings']` now
   carries every threshold (duplicate window, overdue/credit/unapproved days,
   supplier min-txns + dominance, outlier min-txns/multiple/min-amount, capital
   pre-filter gates, duplicate-contact name-sim + flag threshold, generic-email
   ignore, inactive days, LLM min-confidence, misallocated materiality +
   vague-code watch-list). Built as `AuditSettings` (single source of truth),
   threaded through the orchestrator into every deterministic + LLM + contact
   check. Defaults match the old constants, so behaviour is unchanged unless a
   client overrides. Covered by `tests/test_audit_settings.py`.
2. **Action buttons** — *local-state set DONE:* Dismiss, **Snooze/Ignore-30**,
   **Mark-OK** (accept legit, ≠ dismiss), and **bulk** (dismiss/snooze/mark_ok)
   — all live (`POST /trapped/{id}/snooze|mark-ok/`, `POST /trapped/bulk/`),
   snoozed rows auto-reappear after the window, covered by
   `tests/test_action_buttons.py`. *Still stubbed (need sir + real Nango write):*
   the Xero write-backs — Void / Approve / Delete / Create-Credit-Note /
   Change-account→Save (resolve/apply-ai-fix exist but the PUT is a stub).
3. **Contact Defaults (4 fields)** — unlocks default-based Unexpected Account/Tax.
4. **Bank set** (now unblocked) — Bill/Invoice-or-Direct matching, Bank Balance, per-account Unreconciled/Unprocessed.

See [DOC_VS_REALITY.md](DOC_VS_REALITY.md) for the full evidence and
[GAP_ANALYSIS.md](GAP_ANALYSIS.md) for the original gap list.
