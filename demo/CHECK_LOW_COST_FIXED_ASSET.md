# Check Spec — Low-Cost Fixed Assets (+ Capital Item Review sibling)

What Xenon's "Low Cost Fixed Assets" does, and our version. We built this
**AI-assisted** (more nuance than a flat threshold). The sibling is **Capital
Item Review** (the reverse: a big expense that should be an asset) — same engine.

---

## What it is (Xenon)
Flags transactions coded to a **Fixed Asset** account whose **value is low** —
i.e. it should probably be an **expense**, not capitalised.

**Transactions checked:** customer invoice lines, supplier bill lines, Money In,
Money Out.

**Threshold:** a value **under which** it's "too small to be an asset" — set per
the org's capitalisation policy.

**Note (Xenon):** several low line items on one bill may *cumulatively* exceed
the threshold → the bill as a whole might be a legit asset; the check is to
*verify*, not assert.

## Xenon's features
| Feature | What it does |
|---|---|
| **Details** | Type · Contact · Date · Description · Net Amount · Account · **"Change to"** (pick a better account, e.g. a P&L expense) |
| **Change account → Save** | write the corrected account back to Xero |
| **Edit in Xero** | if reconciled / payment / credit allocated → can't change via API, deep-link instead |
| **View in Xero** | deep-link to the transaction |
| **Dismiss / Show Dismissed** | hide "actually correct"; toggle to review |
| **Bulk** | dismiss many |

## Xenon's settings
| Setting | Meaning |
|---|---|
| Asset value threshold | below this on a fixed-asset account → flag (per org capitalisation policy) |

---

## Our logic (what's actually built) — AI-assisted
- **Pre-filter pool** `low_asset`: account type is **asset/fixed-asset** AND `0 < amount < £10,000`.
- Send to the **LLM** → verdict `capitalise` / `expense` / `correct`.
- Keep only **`expense`** verdict (a low_asset can only become `low_cost_fixed_asset`) with **confidence ≥ 0.80**; pool guard drops contradictory verdicts.
- Emits `low_cost_fixed_asset` (medium).

**Built today ≈ ~90%** (detection). Difference vs Xenon: Xenon uses a **flat
amount threshold**; we use **AI judgement** (reads description) — more nuance,
but model-dependent, so we keep a confidence cutoff + pool guard.

## Edge cases
- **FP — bulk buy:** 100 chairs @ £500 (total £50k) — unit small, total asset-size → keep as **review**, not assert.
- **FP — genuinely small but legit on asset account** (e.g. a part of a larger asset).
- **FN — repair vs improvement:** £X work that could be expense *or* capital — judgement, so wording must be "review"; AI can err → confidence cutoff guards.
- **FN — cumulative bill:** many small lines summing over threshold → per-line view misses; should also look at the bill total.

## Configurable settings (target)
| Setting | Xenon | Ours now | To do |
|---|---|---|---|
| Asset threshold | ✅ (per policy) | `_CAPITAL_PRE_FILTER_MAX` const (£10k pre-filter) | per-client / per-country |
| AI confidence cutoff | (n/a — rule-based) | `0.80` const | per-client (optional) |
| Watch-list / cumulative-bill | — | ❌ | consider bill total, not just per-line |

## Logic (pseudo)
```python
for tx in documents:                      # incl. Money In/Out
    if asset_account(tx) and 0 < tx.amount < THRESHOLD:
        pool('low_asset', tx)
for (kind, tx), v in AI(pool):
    if v.verdict != 'expense' or v.confidence < 0.80: continue   # pool guard + cutoff
    emit(tx, low_cost_fixed_asset, suggest='expense account')
```

---

## Status (what we have vs to build)
- ✅ **Detection built (~90%, AI):** asset-account + low-amount pool → LLM `expense` verdict + confidence cutoff + pool guard. (Live: £80 keyboard on Office Equipment (710) → candidate.)
- ❌ **To build:**
  1. **Per-client / per-country** asset threshold.
  2. **Cumulative-per-bill** consideration (sum of low lines).
  3. **Actions:** Change-account → Save (write-back), Edit-in-Xero, Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon = flat amount threshold on fixed-asset accounts. Ours = **AI** (reads the
description) → catches more nuance, with confidence + pool guards. Gaps = the
per-client threshold and the Save/Edit-in-Xero/Dismiss actions. Sibling **Capital
Item Review** (big expense → asset) shares the same engine.
