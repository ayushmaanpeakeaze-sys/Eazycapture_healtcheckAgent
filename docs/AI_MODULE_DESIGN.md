# AI Module — Design Doc (consolidate + ground)

Status: **Proposal** (no code yet). Two goals:
1. **One folder for all AI** — separate files by concern so a new dev understands
   everything in 10 minutes.
2. **Grounded generation** — the LLM only *phrases* facts the deterministic
   checks already computed. No hallucination, no RAG.

---

## 1. Current state — AI is scattered across 9+ places
| File | Does | Problem |
|---|---|---|
| `app/core/llm.py` | Groq client (timeout/retries) | infra mixed into `core` |
| `app/services/enrichment_service.py` | builds prompt, calls LLM, parses | the real logic, buried |
| `app/schemas/enrichment.py` | request/response models | far from its service |
| `app/api/routers/enrichment.py` | `/enrich-audit` endpoint | another folder |
| `app/modules/healthcheck/ai_client.py` | an AI client | duplication risk |
| `app/modules/healthcheck/issue_context.py` | deterministic so-what/solution | not seen as "AI layer 1" |
| `app/services/healthcheck/llm_rules.py` | LLM checks (wrong_category, anomaly) | yet another folder |
| `app/modules/healthcheck/services/{reenrich,apply_ai_fix,suggest_fix}_service.py` | AI-adjacent services | spread out |
| `app/modules/insights/*` | KPI snapshots (separate concern) | leave as-is |

A new dev cannot answer "where is the AI?" — it's everywhere. **Consolidate.**

> Current grounding is already *partial-good*: `enrichment_service` passes the
> deterministic finding + `flagged_detail` (truncated 400 chars), `temperature=0`,
> `response_format=json_object`. We tighten this (complete facts + strict schema),
> not rebuild it.

---

## 2. Target — one module: `app/modules/ai/`

```
app/modules/ai/
├── README.md            ← START HERE. What each file is + the flow diagram.
├── __init__.py          ← public exports (generate_insight, run_llm_checks)
├── client.py            ← THE LLM client. Groq config, timeout, retries, kill-switch.
│                          (moved from core/llm.py — one place owns the model)
├── schemas.py           ← ALL AI Pydantic models: Facts, InsightRequest, Insight,
│                          LlmCheckResult. Strict output schemas live here.
├── facts.py             ← builds the GROUNDED facts object for one flagged issue
│                          (from the check's match_reasons + the actual rows).
├── templates.py         ← Layer-1 deterministic so-what/solution per issue_type
│                          (moved from issue_context.py). NO LLM.
├── prompts.py           ← system + user prompt builders. One place, reviewable.
├── insight_service.py   ← orchestrator: facts → (template OR grounded LLM) → cache.
├── checks_llm.py        ← LLM-based CHECKS (wrong_category, anomaly) (from llm_rules.py).
├── cache.py             ← Redis get/set for `health_check_ai:{id}` (+ TTL).
└── router.py            ← AI HTTP endpoints (/enrich, /ai-insight) (from api/routers).
```

### New-dev reading order (put this in README.md)
1. `README.md` — the picture.
2. `facts.py` — what data the AI is *allowed* to see (the grounding contract).
3. `templates.py` — the 80% case that needs NO LLM.
4. `prompts.py` — exactly what we ask the model.
5. `insight_service.py` — how it all wires together.
6. `client.py` / `cache.py` — the plumbing.

Everything AI = this one folder. `insights/` (KPIs) stays separate — it's
reporting, not the check-explanation AI.

---

## 3. Grounded generation — the anti-hallucination contract

**Rule: the LLM phrases, it never computes or recalls.** Every number it states
must be present in the `Facts` we hand it.

```
deterministic check ─▶ facts.build(issue, rows)  ─▶ STRICT Facts object
        │                                              (ids, amounts, dates,
        │                                               contact, confidence, reason)
        ▼
templates.get(issue_type)  ── deterministic? ──▶ return template (NO LLM)  ← Layer 1
        │ needs nuance
        ▼
prompts.build(facts) ─▶ client.complete(schema=Insight, temperature=0) ─▶ Insight
        ▼
cache.put(id, insight)        ← Layer 3
```

### facts.py — the grounding contract (the key file)
Builds a **complete, untruncated** facts object for the issue from data the check
already isolated. Example (duplicate bill):
```json
{
  "issue_type": "duplicate_bill",
  "a": {"number":"DUP-99","amount":"500.00","date":"2026-06-01","contact":"ABC Ltd"},
  "b": {"number":"DUP-99","amount":"500.00","date":"2026-06-01","contact":"ABC Ltd"},
  "confidence": 1.0,
  "match_reasons": {"same_reference": true, "same_amount": true, "days_apart": 0}
}
```
The LLM gets exactly this — so it cannot invent a third invoice or a wrong amount.

### prompts.py — strict instruction
```
SYSTEM: You are a UK chartered accountant. Explain the flagged issue in plain
English using ONLY the facts provided. Never state a figure, date, or name not
in the facts. If the facts are insufficient, say "review manually". Output JSON
matching the Insight schema.
```

### schemas.py — strict output (upgrade from json_object → json_schema)
```
Insight = { explanation: str, severity_ai: "low"|"medium"|"high",
            confidence: float 0..1, regulatory_ref: str|null }
```
A schema (not free `json_object`) forces the model into fixed fields → far less
room to drift.

### Layer 1 wins most of the time
The so-what/solution for `duplicate_bill`, `old_unpaid_invoice`, … are **already
deterministic** (templates.py). For those, **the LLM is never called** — instant,
zero hallucination, zero cost. The LLM is reserved for genuinely judgment-heavy
phrasing.

---

## 4. Why NOT RAG (decision, recorded)
RAG = vector search over **unstructured** text. Our data is **structured rows**,
and the check **already knows the exact rows** for each issue (it produced the
flag). So:
- No retrieval step needed (no "find the relevant invoice" — we have its id).
- No vector DB / embeddings infra, no embedding cost, no retrieval-latency.
- No *new* hallucination surface (wrong chunk retrieved).

Grounded generation is **faster, cheaper, and more reliable** than RAG here. RAG
becomes relevant only if we later add free-text "chat with your books" over
unstructured notes — not for structured check explanations.

---

## 5. Migration (no logic rewrite first)
1. **Create `app/modules/ai/`** + `README.md` + `__init__.py`.
2. **Move** (not rewrite): `core/llm.py`→`client.py`, `enrichment_service.py`→
   `insight_service.py`, `schemas/enrichment.py`→`schemas.py`,
   `issue_context.py`→`templates.py`, `llm_rules.py`→`checks_llm.py`,
   `api/routers/enrichment.py`→`router.py`. Fix imports. Tests stay green.
3. **Extract `facts.py`** — pull the (currently inline, truncated) context-build
   into a dedicated, complete facts builder.
4. **Tighten grounding**: complete facts (no 400-char truncation of key fields) +
   strict `json_schema` output + the "only use facts" prompt.
5. **Confirm Layer 1**: deterministic issue types short-circuit to templates (skip
   the LLM) — verify with a test that those make zero LLM calls.

Steps 1–2 are pure moves (low risk, big readability win). 3–5 are the grounding
upgrade. Each shippable independently.

---

### TL;DR
All AI → one folder `app/modules/ai/` with a file per concern (`client`,
`schemas`, `facts`, `templates`, `prompts`, `insight_service`, `checks_llm`,
`cache`, `router`) + a `README.md` a new dev reads first. The model is **grounded**:
checks compute the facts, the LLM only phrases them (strict schema, temp 0, "only
use given facts"), and deterministic issues skip the LLM entirely via templates.
**No RAG** — data is structured and the rows are already known. Migration is
mostly *moving* existing files, then tightening the facts + output schema.
