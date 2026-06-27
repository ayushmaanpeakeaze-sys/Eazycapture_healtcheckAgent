# Check Spec — Misallocated Items

What Xenon's "Misallocated Items" does, and our version. **Status: not built yet
(❌)** — this is a net-new check, but it's straightforward (data already in hand).
Related to our AI **Wrong Category**, but simpler/deterministic.

---

## What it is (Xenon)
Flags high-value transactions coded to a **broad/vague account** that should go to
a **specific** one — e.g. stationery booked to "General Expenses" instead of
"Office Stationery". Vague accounts make reports useless.

**Checked:** bill lines, invoice lines, Money Out, Money In.

**Default watched (vague) accounts:**
- **429 – General Expenses** (Xero)
- accounts containing **"Uncategorised" / "Unapplied"** (QBO: Uncategorised Asset/Expense/Income)
- + more codes addable in settings

**Threshold:** a **materiality** value — only flag if the amount is big enough.

## Xenon's features
| Feature | What it does |
|---|---|
| **Details** | Type · Contact · Date · Description · Amount · Account · **"Change to"** (pick a specific account) |
| **Change account → Save** | write the corrected account back to Xero |
| **Edit in Xero** | if reconciled/allocated → deep-link |
| **View in Xero/QBO** | deep-link to the transaction |
| **Dismiss / Show Dismissed** | hide "actually correct"; toggle to review |
| **Bulk** | dismiss many |

## Xenon's settings
| Setting | Meaning |
|---|---|
| Watched (vague) accounts | which accounts count as "vague" (default 429 / Uncategorised / Unapplied) + additions |
| Materiality threshold | min amount to flag (avoid small-posting noise) |

---

## Our status — ❌ not built
- No `misallocated_item` issue type exists today.
- Closest thing we have: **Wrong Category (AI)** — reads vendor/description and
  suggests a better account. That's broader/AI-driven; Misallocated is a
  **simple deterministic** "vague account + big amount" rule. They complement.

## Edge cases (for when we build it)
- **FP — suspense/clearing account:** legit temporary use until the right account is known → review, not error.
- **FP — small amounts:** below materiality = noise.
- **FN — cumulative build-up:** many small postings (each below threshold) summing to a big vague balance → per-line misses it; also check the **account total**.

## Configurable settings (target)
| Setting | Xenon | Target for us |
|---|---|---|
| Vague-account watch-list | 429 / Uncategorised / Unapplied + config | per-client list (CoA-specific) |
| Materiality threshold | ✅ | per-client |

## Logic (pseudo — target)
```python
VAGUE = client_watchlist or {429, ...} ∪ name_contains('uncategorised','unapplied','suspense','sundry')
for line in document_lines:
    if (line.account in VAGUE) and line.amount >= MATERIALITY:
        emit(line, misallocated_item, current=line.account, suggest=better_account(line))
```

---

## Status (what we have vs to build)
- ❌ **Not built (~0%).**
- ✅ **To build (easy, data in hand):**
  1. **Vague-account watch-list** (429 / Uncategorised / Unapplied + per-client).
  2. **Materiality threshold** (per-client).
  3. Optional: use **Wrong Category (AI)** to fill the "Change to" suggestion.
  4. **Actions:** Change-account → Save (write-back), Edit-in-Xero, Dismiss / Show-Dismissed, bulk.

## Xenon comparison (one line)
Xenon = vague-account watch-list + materiality (deterministic). We don't have it
yet, but it's a small build (watch-list + threshold), and our **AI Wrong
Category** can supply the suggested specific account — a nice combo.
