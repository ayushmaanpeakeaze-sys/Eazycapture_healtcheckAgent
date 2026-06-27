# Healthcheck POC — Demo walkthrough

A literal step-by-step for sir. Reads top-to-bottom; you can hand
this to anyone and they can run the demo without you in the room.

---

## Pre-flight (90 seconds before sir walks in)

```bash
make up                                  # Postgres :5434 + Redis :6379
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 &
.venv/bin/celery -A app.core.celery_app worker --loglevel=info --pool=solo &
make reset-demo                           # ~30 seconds — leaves Demo Co at 15 trapped, all enriched
```

`make reset-demo` confirms the demo state. When it prints
`enriched 15/15 trapped rows`, you're ready.

---

## Demo flow

### 1. Stage-set (10 seconds)

> "Sir, this is the new healthcheck POC. It's a standalone FastAPI
> backend running on port 8001. Own Postgres, own Redis, calls back
> into our existing rules engine over HTTP for the LLM work. It
> doesn't touch the Django monolith at all."

Show: the running uvicorn log, the docker-compose stack, the
`/health` returning `{"status":"ok"}`.

### 2. Demo Co exists with seeded invoices (15 seconds)

> "Demo Co has 22 seeded invoices — mix of Hamilton Smith duplicates,
> Net Connect drift, future-dated bills, missing invoice numbers,
> capital items and one PAID-but-partial. Real Xero structure, not
> mocked."

Show: `make psql` then
`SELECT vendor_name, invoice_number, amount, status, type FROM invoice WHERE company_id='1a55c9dc-c48d-4ef6-a828-29d0298ebebd' ORDER BY vendor_name LIMIT 10;`

### 3. Trigger an audit (10 seconds)

> "One POST kicks off the audit. Returns 202 with a batch id in
> under 100ms. The actual work runs in a Celery worker."

Show:

```bash
curl -X POST http://localhost:8001/api/v1/health/sync-xero-history/1a55c9dc-c48d-4ef6-a828-29d0298ebebd/
```

Response is `{"batch_id":"…","status":"in_progress"}`.

### 4. Watch the progress stream (15 seconds)

> "Frontend polls this status URL. Stage label progresses live —
> dispatched → fetching → shaping → auditing → persisting →
> completed. ~8 seconds total."

Show:

```bash
watch -n1 'curl -s http://localhost:8001/api/v1/health/sync-xero-history-status/<batch_id>/ | jq "{status, stage, stage_label, total, trapped}"'
```

Final state: `status=completed total=22 trapped=15`.

### 5. Trapped feed (20 seconds)

> "Frontend hits this every 2s to render the trapped-rows table."

Show:

```bash
curl -s "http://localhost:8001/api/v1/health/trapped-invoices/?company_id=1a55c9dc-c48d-4ef6-a828-29d0298ebebd&limit=3" | jq
```

Point out:

- Hamilton Smith INV-0001 + INV-0005 (the duplicate pair)
- `ai.severity_ai`, `ai.explanation`, `ai.regulatory_ref`
- `xero_url` — clickable deep-link straight into Xero
- All 15 rows enriched after `make reset-demo`

### 6. Suggest fix → Apply AI fix (30 seconds)

> "Click a row → modal calls `/suggest-fix` → AI returns a structured
> fix plan with `field_updates`. Click 'Apply AI Fix' → backend
> parses, validates, marks resolved."

Show:

```bash
ROW=$(curl -s "http://localhost:8001/api/v1/health/trapped-invoices/?company_id=1a55c9dc-c48d-4ef6-a828-29d0298ebebd&limit=1" | jq -r '.results[0].id')
curl "http://localhost:8001/api/v1/health/trapped/$ROW/suggest-fix/?company_id=1a55c9dc-c48d-4ef6-a828-29d0298ebebd" | jq .suggestion
curl -X POST "http://localhost:8001/api/v1/health/trapped/$ROW/apply-ai-fix/?company_id=1a55c9dc-c48d-4ef6-a828-29d0298ebebd" -d '{}' | jq
```

Response: `resolved=true ai_applied=true ai_fix_strategy=void_duplicate`.

### 7. Audit summary banner (15 seconds)

> "While the per-row enrichment was running, the LLM also produced
> the batch-level summary."

Show:

```bash
curl -s "http://localhost:8001/api/v1/health/sync-xero-history-status/<batch_id>/" | jq .audit_summary
```

Read the narrative + top themes + suggested cleanup order out loud.

### 8. Multi-tenant panorama (15 seconds)

> "And here's the cross-tenant dashboard view — health score per
> company, worst first."

Show:

```bash
curl -s "http://localhost:8001/api/v1/health/companies-panorama/" | jq
```

Demo Co has a health_score; Test Co's is null (never audited).

### 9. Stub mode → live mode (20 seconds)

> "Right now the actual Xero PUTs are stubbed — the worker logs
> 'STUB Xero call — would_apply {…}' instead of calling Nango. The
> moment we get the Nango secret key, one env var sets it live, and
> every resolve becomes a real Xero update. Zero code change."

Show:

```bash
grep "STUB Xero call" <worker_log> | tail -3
```

### 10. Open questions for sir (30 seconds)

> "Three things from you to take this from POC to production:
>
> 1. Nango secret key + webhook secret — unblocks live mode.
> 2. End-user id convention for Nango — we currently use the company
>    UUID; happy to switch.
> 3. Django's JWT_SECRET — drop it in our env and the same tokens
>    validate here.
>
> Everything else — rate limiting, structured logging, Sentry,
> CI/CD — is standard SRE work, about a week. The POC validates the
> design works."

---

## If sir asks…

| Question | Answer |
|---|---|
| Why is resolve stubbed? | "No Nango secret yet. `ResolveService._call_xero` branches on `nango.is_available()` + company connection. Set the key, every resolve PUTs to Xero. Zero code change." |
| Why is auth optional? | "POC mode for fast demos. `JWT_SECRET` enables strict JWT verification, and `AUTH_DISABLED=true` forces the open demo path. Same secret Django uses → existing tokens just work." |
| What's missing for production? | "Per-company rate limiting, structured logging, Sentry/Datadog, CI/CD, DB backups, SSL termination. ~1 week of SRE work." |
| What if Nango goes down? | "Graceful fallback — audit drops back to seeded data, resolve falls back to stub log. Status endpoint surfaces `error`. Never crashes the worker." |
| Multi-tenant — what's the guarantee? | "Every repository query takes `company_id`. Even single-row fetches by primary key filter on it. Cross-tenant tests assert no leak." |

---

## When the demo is done

- `make down` to stop the docker stack if you're sharing the laptop.
- Leave `make reset-demo` documented; the next walkthrough is one
  command.
