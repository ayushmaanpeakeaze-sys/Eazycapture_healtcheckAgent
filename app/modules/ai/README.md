# `app/modules/ai/` — the AI module

**Everything LLM-touching lives in this one folder.** Previously this code was
scattered across `core/`, `services/`, `api/`, `schemas/`, and `modules/healthcheck/`.
It is now consolidated so you can understand the whole AI surface in one place.

## What the AI does here
Two jobs:
1. **Explain flagged issues** to the user ("so what" + "how to fix") — mostly
   deterministic templates, LLM only for nuance.
2. **A few LLM-based checks** (category guess, anomaly review) — the rest of the
   ~30 checks are deterministic and live in `services/healthcheck/`.

## File map
| File | What it is |
|---|---|
| `client.py` | The Groq LLM client — config, timeout, retries. **One place owns the model.** |
| `schemas.py` | All AI Pydantic models (request/response, the `Insight` output shape). |
| `templates.py` | **Deterministic** so-what/solution text per `issue_type`. No LLM — instant, never hallucinates. The 80% case. |
| `facts.py` | **The grounding contract.** `build_row_facts(row)` = the exact, complete facts the LLM is allowed to see for one flagged row. Read this to know "what does the AI actually know?". |
| `prompts.py` | Every LLM prompt string in one place. Change *what we ask the model* here. |
| `insight_service.py` | Per-row enrichment: `build_row_facts` → prompt → LLM (temp 0, JSON) → structured insight → Redis cache. |
| `checks_llm.py` | The LLM-based **checks** (`wrong_category`, `anomaly`). Called by the health-check orchestrator. |
| `router.py` | The HTTP endpoints (`/enrich-audit`, etc.), mounted under `/api/v1`. |
| `__init__.py` | Public exports (`get_groq`, `close_groq`, `get_context`). |

## Reading order (new dev: 10 minutes)
1. **This README** — the picture.
2. `templates.py` — the deterministic explanations (no LLM). Most issues end here.
3. `schemas.py` — the shapes the LLM must fill.
4. `insight_service.py` — how a row becomes an insight (the LLM path).
5. `client.py` — the model client.
6. `checks_llm.py` — the LLM checks.

## The grounding principle (why the LLM doesn't hallucinate)
**The LLM phrases facts; it never computes or recalls them.** The deterministic
check already found *which* rows are involved and *why*. We hand the LLM exactly
those facts and ask only for plain-English wording:

```
check ─▶ exact facts (ids, amounts, dates, contact, confidence, reason)
     ─▶ templates.get_context(issue_type)  ── deterministic? ──▶ return (NO LLM)
     ─▶ else: LLM with temp 0 + JSON + "use ONLY these facts" ─▶ Insight
     ─▶ cache in Redis (health_check_ai:{id})
```

No RAG / vector DB — the data is structured and the rows are already known.

## Done
- Consolidation — all AI files moved into this one folder.
- `facts.py` — extracted; passes the **complete** flagged detail (no more 400-char
  truncation of the grounding), unit-tested in `tests/test_ai_facts.py`.
- `prompts.py` — all prompt strings pulled out of the service.

## Still planned (smaller follow-ups)
See `docs/AI_MODULE_DESIGN.md`:
- `cache.py` — extract the Redis get/set helpers (currently inline in the service).
- Upgrade output from `json_object` → strict `json_schema` (pending Groq support
  check; the `_record_from_item` validation already constrains the shape).

## What is NOT here
`app/modules/insights/` — that's KPI/dashboard **reporting** (different concern),
left separate on purpose.
