# Check Spec — Multi-Tax Code Suppliers

> **Same check as [Multi-Account Suppliers](CHECK_MULTI_ACCOUNT_SUPPLIERS.md), on
> the tax field.** HISTORY-based: a supplier's tax code vs the tax codes it
> **usually uses** (not a default). One engine, same 3-month lookback,
> Mark-OK/bulk actions. This page lists only what differs.

---

## What it is (Xenon)
Flags supplier contacts whose transactions use **more than one tax code** — the
odd one is often a slip (and tax slips hit the VAT return).

**Checked:** supplier bill lines, Money Out — vs the supplier's **previously-used
purchase tax codes**.

**Different from Unexpected Tax Code:** Multi-Tax compares to the contact's
**own history**; Unexpected compares to the **saved default**.

## Date range, features & settings — identical to Multi-Account
Period + **3-month lookback** (configurable). Details (Contact · Type · Date ·
Reference · Value · **Tax Code** · Account Code · Description). View/Edit in Xero ·
Mark-OK / Mark-all-OK · Show-OK / Mark-Not-OK · Bulk mark-OK.

## What differs from Multi-Account (only this)
| | Multi-Account | Multi-Tax |
|---|---|---|
| Field | account code | **tax code** |
| Our issue type | `multi_account_supplier` | **`multi_tax_code_supplier`** |
| Compared to | usual accounts | **usual tax codes** |
| Severity | MEDIUM (reporting) | **HIGH (VAT return)** |
| Compare by | account meaning | **rate/meaning, not label** |

Everything else — group by contact, dominant ≥70%, min txns, flag outliers,
3-month lookback (to-do), multi-category whitelist (to-do) — is the **same shared
logic**.

## Status (same as Multi-Account)
- ✅ Detection built (~80%) — dominant tax-code pattern, flag outliers. (Live: Net Connect bills mostly INPUT2, one TAX001 → flagged.)
- ❌ To build (shared): **3-month lookback**, compare by **rate** not label, per-client dominance/min-txns, Mark-OK / Show-OK / bulk actions.

## Xenon comparison (one line)
Mirror of Multi-Account on tax — **HIGH severity** (VAT) and **compare by rate
not label**. Same plan: add 3-month lookback + Mark-OK/bulk actions.
