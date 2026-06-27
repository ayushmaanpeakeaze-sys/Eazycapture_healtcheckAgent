# Check Spec — Unreconciled Bank Items

What Xenon's "Unreconciled Bank Items" does, its features, and exactly what we
can build (and the one Xero limit). Use this as the build spec for the check.

---

## What it is (Xenon)
Identifies, **per bank account**, how many bank transactions in Xero still
need **explaining, reconciling, or both**. Only bank accounts that *have* items
to deal with appear; clean accounts are hidden.

## Xenon's features (what it actually does)
1. **Per-account listing** — one row per bank account that has ≥1 unreconciled item; clean accounts don't show.
2. **Count of items** needing attention, per account.
3. **"Process" button** → opens that account's **Bank Reconciliation page in Xero** (fix happens in Xero, not in the tool).
4. **Feeds the Bookkeeping Health Score** — the quantity + age of unreconciled items pull the health % down; as they're cleared in Xero, the % improves.
5. **Ignore a bank account** — exclude an account from the check (e.g. a personal account connected to Xero), via a red-cross on the row OR Settings → Bookkeeping Quality Checks → Unreconciled Bank Transactions → Exclude the following bank accounts.
6. **Reinstate** a previously-ignored account (remove it from the exclude list).

---

## Data source + the Xero limit (important)
- Xero's API gives each **BankTransaction** an **`IsReconciled`** flag → we can
  count/age the ones that are `false`, **per bank account**.
- Xero does **NOT** expose the raw bank-statement feed / unmatched statement
  lines (official, regulatory). So the *true* "items to reconcile" (the feed)
  isn't available to anyone via the API — **Xenon screen-scrapes** it; via API
  we (and Xenon's API path) can only show the `IsReconciled=false` count.
- **Net:** we can match Xenon's API-level capability (count + age + per-account
  + exclude + link to Xero), just not the raw feed lines.

---

## Our version — what to build
| Feature | How |
|---|---|
| Per-account unreconciled **count** | group `BankTransactions` (IsReconciled=false) by `BankAccount.AccountID` |
| **Age** (oldest unreconciled) | min `Date` of the unreconciled items per account |
| **Total value** at risk | sum `Total` of unreconciled items per account |
| **Feed the health score** | add count (weighted by age) into the blended health-score numerator |
| **"Process" link** | deep-link to Xero's bank-rec screen for that account (shortcode + account) |
| **Exclude / reinstate accounts** | per-client setting in `audit_config` (e.g. `excluded_bank_accounts: [accountId,...]`) |
| **Stale-feed warning** (our extra) | if an account's newest bank txn is > 14 days old → "connection may be broken" |

## Edge cases
- **False positive — reconnect backlog:** feed reconnects and months of old items arrive at once; age looks old but it's a fresh backlog. → show age but don't over-penalise; note "recently imported".
- **False positive — today's items:** 1–2 day-old items are normal pending reconciliation, not a problem.
- **False negative — reversal pair:** a debit and an equal-amount credit both show unreconciled; should be paired, else counted twice.
- **False negative — excluded-but-active:** an ignored personal account that's actually used for business → won't show; review the exclude list.

## Configurable settings
- **Exclude bank accounts** (default none) — per-client list; personal/owner accounts skipped.
- **Stale-feed window** (default 14 days) — silence → "connection broken" warning.
- **Age→severity curve** — how fast old items escalate severity (and pull the score down).

## Logic (pseudo)
```python
unreconciled = [t for t in bank_txns if not is_reconciled(t)]
for account, items in group_by(unreconciled, bank_account_id):
    if account in excluded_bank_accounts:        # per-client setting
        continue
    emit(account,
         count=len(items),
         total=sum(i.total for i in items),
         oldest=min(i.date for i in items),
         severity=by_age(oldest))
for account where days_since_last_txn(account) > STALE_FEED_DAYS:
    emit(account, stale_feed, "connection check karo")
```

---

## Status (what we have today vs to build)
- ✅ **Already built (Insights snapshot):** firm-wide + per-client `bank_reconciliation`
  = `unreconciled_count`, `unreconciled_value`, `last_reconciled_date`,
  `most_recent_transaction` (via `IsReconciled`). Shown in panorama + org view.
- ❌ **To build for this check:**
  1. **Per-bank-account** breakdown (we currently aggregate per company, not per account).
  2. **Exclude-accounts** setting (in `audit_config`) + reinstate.
  3. **Feed the health score** (age-weighted).
  4. **"Process" deep-link** to Xero's reconcile screen per account.
  5. **Stale-feed** warning.

## Xenon comparison (one line)
Same API limit as everyone — count + age + per-account + exclude + link to
Xero. We match that via `IsReconciled`; our extras = stale-feed warning +
gap→root-cause linking in Bank Balance Check. Raw statement lines: nobody gets
them via API (Xenon screen-scrapes).
