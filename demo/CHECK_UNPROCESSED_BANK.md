# Check Spec — Unprocessed Bank Items

> Closely related to [Unreconciled Bank Items](CHECK_UNRECONCILED_BANK.md).
> "Unprocessed" = bank-feed lines **still showing on the feed, needing
> explaining** (a subset of unreconciled). **Xero-only.** This page adds the
> Unprocessed-specific features + the key API-limit confirmation.

---

## What it is (Xenon)
Per bank account, the count of bank transactions **showing on the Xero feed but
not yet explained**. Accounts with 0 issues are hidden — **unless** the account
hasn't updated in **> 2 weeks** (stale feed). Quantity + age feed the health score.

## ⚠️ Key confirmation — Xero API can't give this
Xenon's own docs: pulling the unprocessed list **requires the Xenon Connect
Browser Extension** (Chrome/Edge), and **a normal Xero sync does NOT refresh
these values** — only the extension does. → This **confirms** the raw feed lines
are **not available via Xero's API** (Xenon **screen-scrapes** them). So via API,
nobody (us or Xenon's API path) gets the true feed; we use the `IsReconciled`
count instead.

## Xenon's features
| Feature | What it does |
|---|---|
| **Per-account count + age** | one row per account with items; oldest age |
| **Process** | deep-link to that account's Xero reconcile screen |
| **Browser-extension update** | pull the live unprocessed list (icons per account) |
| **View & Download** | view all unprocessed txns; download CSV / PDF |
| **Notes (+ tag users)** | notes attached to the bank account (permanent) |
| **Ignore / Reinstate account** | exclude e.g. a personal account; settings list |
| **Stale-feed** | shows the account if feed silent > 2 weeks |

## Xenon's settings
- **Exclude bank accounts** (per-client). · Stale-feed window (~2 weeks).

---

## Our logic / status
- Same data limit as Unreconciled — we can only use **`IsReconciled=false`** count
  per account (BankTransactions), **not** the raw feed lines.
- **Already built (Insights snapshot):** company-level `bank_reconciliation`
  (`unreconciled_count`, `last_reconciled_date`, `most_recent_transaction`).
- ❌ **To build:** per-**account** breakdown, **exclude-accounts** setting,
  **health-score feed**, **Process** deep-link, **stale-feed (14-day)** warning,
  notes/tagging, CSV/PDF download.

## Edge cases
- **FP — reconnect backlog:** months of old items arrive at once (age old, but fresh backlog).
- **FP — today's items:** 1–2 day-old = normal pending.
- **FN — reversal pair:** debit + equal credit both unprocessed → should pair.

## Xenon comparison (one line)
Same API limit for everyone — count + age + per-account + exclude + link to Xero.
Xenon screen-scrapes the feed via a **browser extension** (API can't give it); we
match the API-level capability via `IsReconciled` and add a stale-feed warning.
Build = per-account split + exclude setting + health-score feed (same as the
Unreconciled Bank check).
