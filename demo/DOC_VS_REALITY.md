# Doc vs Reality — Health Checks (what's actually built)

Verified against the live code (`app/services/healthcheck/`). The Hinglish "sir"
doc describes the **target design**; this sheet says what is **actually live**
today, so nothing is over-claimed in front of sir.

Legend: ✅ built (close to doc) · 🟡 partial · ❌ not built yet

---

## ⚠️ One honest caveat up front — "Configurable settings"
The doc's headline ("har threshold ek per-client settings file me hai") is the
**biggest gap**. **Reality:** the only per-client config today is
`disabled_rules` + `ignore_before` (in `audit_config`). Every threshold
(7-day dup window, 60-day overdue, 70% dominance, 4× outlier, weights…) is a
**hardcoded constant**, NOT per-client. → Present configurable settings as
**roadmap / architecture-ready**, not a live feature.

---

## Per-check status

| # | Check | Status | Actually in code | Gap vs doc |
|---|-------|--------|------------------|------------|
| 1 | Duplicate Contacts | ✅ ~90% | blocking + weighted score (tax .45/bank .35/email .30/phone .15/name .10), VAT-differ reject, customer/supplier split | generic email/phone ignore **not** there; threshold not per-client |
| 2 | Duplicate Invoices/Bills | ✅ ~90% | 7-day window, ref tiers 0.97/0.95/0.85, recurring exclusion, credit-aware, direction-aware | "alag-ref → review" is currently **dropped** (not a review); value-tolerance not there; settings not per-client |
| 3 | Old Unpaid Inv/Bills | ✅ ~90% | due-date based, 60-day, outstanding net of paid | age not per-client; ignore-for-30-days (UI) |
| 4 | Old Sales/Purchase Credits | ✅ ~85% | credit-type, outstanding, age | uses the **invoice/bill age** const, no separate credit age; small-remainder cutoff |
| 5 | Unapproved Inv/Bills | ✅ ~90% | DRAFT/SUBMITTED, 7-day grace, last-touch date | grace not per-client |
| 6 | Data-integrity (paid-but-due, overpaid, future, missing vendor/ref) | ✅ ~90% | all present | future-buffer / overpaid-tolerance not configurable |
| 7 | Wrong-Direction Account | ✅ ~85% | allowed account-types per direction | **contra/discount whitelist not there** (Sales Returns etc. could false-flag) |
| 8 | Sales-Tax-on-Bills / Purchase-Tax-on-Invoices | ✅ ~85% | uses Xero CanApplyToExpenses/Revenue flags, scans all lines | reverse-charge whitelist not there |
| 9 | Wrong Category (AI) | ✅ ~90% | LLM + COA + direction guard + confidence + dedupe | — |
| 10 | Capital / Low-Cost (AI) | ✅ ~90% | pools + LLM verdict + confidence cutoff + pool guard | asset threshold not per-client |
| 11 | Amount Outlier | ✅ ~90% | per-contact median, 4× multiple, min amount/txns, LLM anomaly | multiple not per-client |
| 12 | Opening Balance Differences | ✅ ~70% | account 840 OR name "historical adjustment/opening balance" | **Companies House comparison not there** (needs external data) |
| 13 | Sales/Purchase Tax Missing | 🟡 ~55% | flags zero/no-VAT lines | **no org-VAT gate, no account-type filter, no ignore-list** (wages/pension) → noisy |
| 14 | Multi-Account / Multi-Tax Suppliers | 🟡 ~80% | dominant ≥70%, min txns, flag outliers | **no lookback months** (only current batch); no multi-category whitelist |
| 15 | Contact Defaults | 🟡 ~50% | supplier purchase-account / customer sales-account missing | **tax-code defaults not checked** (2 of 4); history-suggest |
| 16 | Inactive Contacts | 🟡 ~45% | flags contacts not in audited txns | **name-match based, not real last-transaction date**; no new-contact grace |
| 17 | Unexpected Account / Tax | 🟡 ~40% | **frequency** outlier (used once, batch ≥100) | doc wants **DEFAULT-based** (compare to contact default) — different approach |
| 18 | Bill-or-Direct / Invoice-or-Direct | 🟡 ~30% | only the **proxy** (authorised doc, no number) | real bank-spend→open-bill **matching not built** (bank txns now fetched, so now possible) |
| 19 | Misallocated Items | ❌ | not built | whole check (vague-account watch-list + materiality) |
| 20 | Unprocessed Bank Transactions | ❌ | not built as a rule | per-account count/age + stale-feed warning |
| 21 | Unreconciled Bank Transactions | ❌ | not built as a rule | per-account + near-match (TDS) hint |
| 22 | Bank Balance Check | ❌ | not built | Xero bank balance vs accounts balance gap |

> Note: bank **reconciliation summary** (unreconciled count / last reconciled /
> most recent txn) IS built in the **Insights snapshot** (via `IsReconciled`),
> but the three bank **health-check rules** (#20–22) are not.

---

## Can we build the whole thing? — honest verdict

**Yes — ~95% is buildable in-house. Only ONE hard external limit.**

| Bucket | Buildable? | Notes |
|---|---|---|
| Make all thresholds **per-client configurable** | ✅ Yes | extend `audit_config` (engine already reads it for disabled_rules) — straightforward |
| Missing whitelists (contra, reverse-charge, generic email/phone, multi-category) | ✅ Yes | small per-check additions |
| Contact tax-code defaults (#15), separate credit age (#4), alag-ref review (#2) | ✅ Yes | small |
| Inactive real last-txn date (#16), multi-account lookback (#14), tax-missing gating (#13) | ✅ Yes | medium |
| Unexpected **default-based** (#17) | ✅ Yes | needs Contact Defaults populated first (chicken-egg, but doable) |
| Misallocated Items (#19) | ✅ Yes | medium — watch-list + materiality |
| **Bill-or-Direct real matching** (#18) | ✅ Yes | **now unblocked** — we just wired BankTransactions fetch |
| **Bank Balance Check** (#22) | ✅ Yes | BankSummary/Trial-Balance balance vs accounts — data available |
| Unprocessed/Unreconciled Bank **count+age** (#20–21) | ✅ Yes (limited) | via `IsReconciled`; matches what Xenon can show |
| Raw bank **statement-line** reconciliation (the true feed) | ❌ **No** | **Xero does not expose it** (official) — permanent limit for EVERYONE, incl. Xenon (they screen-scrape) |
| Opening-Balance **Companies House** comparison (#12) | ⚠️ Yes, extra | needs a Companies House API integration (external) |

### Bottom line for sir
> "**Saari ~22 checks ban sakti hain** — data ya to already aa raha hai ya Xero
> deta hai. Abhi **~12 built/near-done, ~6 partial, ~4 pending**. Sirf **ek cheez
> permanently limited hai — raw bank statement feed** (Xero kisi ko nahi deta;
> Xenon bhi screen-scrape karta hai). Aur **per-client configurable settings**
> abhi roadmap hai (architecture ready). Baaki sab engineering effort hai, koi
> blocker nahi."

### Suggested build order (after sir's go)
1. **Per-client configurable settings** (extend `audit_config`) — unlocks the doc's headline claim.
2. **Quick wins**: whitelists (#7, #8), contact tax defaults (#15), credit age (#4), alag-ref review (#2).
3. **Bank set** (now unblocked): Bill-or-Direct matching (#18), Bank Balance (#22), Unreconciled count (#20–21).
4. **Medium**: tax-missing gating (#13), inactive last-txn (#16), multi-account lookback (#14), unexpected default-based (#17), misallocated (#19).
5. **External (optional)**: Opening-Balance Companies House (#12).
