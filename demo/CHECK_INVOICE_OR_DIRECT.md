# Check Spec — Invoice or Direct Deposit

> **Same check as [Bill or Direct Payment](CHECK_BILL_OR_DIRECT.md), receive
> side.** Bank **deposit** coded directly to an account while an unpaid **sales
> invoice** exists for the same customer. Same logic/settings/actions, same
> unblock (BankTransactions RECEIVE now fetched). This page lists only what
> differs.

---

## What it is (Xenon)
Finds a **bank deposit coded directly to an account** (not through the customer
ledger) while an **unpaid sales invoice** exists for the same customer. Likely
the same receipt booked twice → invoice stays "unpaid" (**profit overstated**)
and you may chase a customer who already paid.

## Xenon features & settings — identical to Bill or Direct
View (invoice or deposit) · Dismiss Match · Show Dismissed · Bulk dismiss.
Setting: deposit ≤ **X days** after the invoice (default **30**).

## What differs from Bill or Direct (only this)
| | Bill or Direct (purchase) | Invoice or Direct (sales) |
|---|---|---|
| Bank side | **SPEND** (money out) | **RECEIVE** (money in / deposit) |
| Matched against | open **bill** | open **sales invoice** |
| Contact | supplier | **customer** |
| Risk | profit understated / pay supplier twice | **profit overstated / chase a paid customer** |
| Our proxy issue type | `bill_or_direct_booking` | `invoice_or_direct_booking` |

Everything else — group by contact, match deposit↔open invoice (amount ≈,
deposit date ≥ invoice date, within 30 days), whitelist legit direct deposits
(e.g. interest, refunds), tolerance — is the **same shared logic**.

## Status (same as Bill or Direct)
- 🟡 **~30% (proxy):** `invoice_or_direct_booking` = authorised invoice with no number.
- 🔓 **Unblocked:** RECEIVE bank transactions now fetched.
- ❌ **To build:** real **bank-receive → open-invoice** match (within 30 days), whitelist, View / Dismiss / Show-Dismissed / bulk actions.

## Xenon comparison (one line)
Mirror of Bill or Direct on the receive side. Same plan: build the real
deposit→open-invoice match (now unblocked), add whitelist + actions.
