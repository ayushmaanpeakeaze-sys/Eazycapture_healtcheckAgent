# Check Spec — Capital Item Review

> **Sibling of [Low-Cost Fixed Assets](CHECK_LOW_COST_FIXED_ASSET.md), reverse
> direction.** A **high-value item on an EXPENSE account** that should probably
> be **capitalised** as a fixed asset. Same engine (`_batched_capital_review`),
> same AI + actions. This page lists only what differs.

---

## What it is (Xenon)
Flags a transaction coded to an **expense** account whose **value is high** →
possibly **capital** in nature (should be a fixed asset). Verify it's coded right.

**Transactions checked:** bill lines, invoice lines, Money Out, Money In.

**Threshold:** a value **over which** it might be capital (per org policy).

**Default watched accounts (Xenon):** capital items often land here by mistake —
- **461 – Printing & Stationery**
- **473 – Repairs & Maintenance**
- (more expense codes can be added in settings)

## Xenon features & settings — identical to Low-Cost
Details (Type · Contact · Date · Description · Amount · Account · "Change to") ·
Change→Save (write-back) · Edit-in-Xero · View · Dismiss / Show-Dismissed · Bulk.
Settings: high-value **threshold** + **list of watched expense accounts** (default 461, 473).

## What differs from Low-Cost Fixed Assets (only this)
| | Low-Cost Fixed Asset | Capital Item Review |
|---|---|---|
| Direction | asset account, **low** value → expense | **expense** account, **high** value → asset |
| Our issue type | `low_cost_fixed_asset` | **`capital_item_review`** |
| Trigger | amount **below** threshold | amount **above** threshold |
| LLM verdict kept | `expense` | **`capitalise`** |

## Our logic (what's actually built) — AI-assisted
- Pre-filter pool `high_expense`: **expense** account AND `amount ≥ £300` AND the account **name suggests something capitalisable** (equipment/furniture/etc.).
- LLM verdict → keep only **`capitalise`** with confidence ≥ 0.80 (pool guard).
- Emits `capital_item_review` (medium).
- **Difference vs Xenon:** Xenon watches **specific account codes** (461/473 + config); we use a **capitalisable-name keyword + threshold**. → adding the 461/473 watch-list would align us.

## Edge cases
- **FP — big-but-genuine expense:** £300k annual insurance / rent advance — high value, not an asset → watch-list of accounts + confidence cutoff guards.
- **FP — bulk buy of cheap items** that's high in total but expensed correctly.
- **FN — repair vs improvement:** judgement call → wording "review", AI can err.

## Status (what we have vs to build)
- ✅ **Detection built (~90%, AI):** high-expense pool → LLM `capitalise` + cutoff + pool guard. (Live: £1,200 "Dell laptop" on an expense account → candidate.)
- ❌ **To build:**
  1. **Watched-accounts list** (default **461, 473** + per-client additions) — align with Xenon.
  2. **Per-client** high-value threshold.
  3. **Actions:** Change-account → Save (write-back), Edit-in-Xero, Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon = high value on **watched expense accounts (461/473)**; ours = **AI** on
high expenses with capitalisable names. Aligning = add the watched-accounts list;
plus the Save/Edit-in-Xero/Dismiss actions. Same engine as Low-Cost Fixed Assets.
