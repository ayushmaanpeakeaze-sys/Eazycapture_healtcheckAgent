-- ============================================================================
--  Bookkeeping Health-Check — live demo queries
--  Run:  psql "postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc" -f demo/sir_demo.sql
--  (or paste any block into TablePlus / DBeaver / pgAdmin)
--
--  NOTE: the database stores only FLAGGED findings (one row per flagged
--  document/contact). The rule LOGIC runs in Python; these queries prove
--  WHAT the engine flagged and WHY (the reasoning lives in result->>'message').
-- ============================================================================
\pset pager off

\echo
\echo ════════ 1. Latest audit run — the headline numbers ════════
SELECT c.name              AS company,
       b.status,
       b.total             AS docs_scanned,
       b.trapped           AS docs_flagged,
       b.contacts_total    AS contacts_scanned,
       b.completed_at
FROM   audit_batch b
JOIN   company c ON c.id = b.company_id
WHERE  b.status = 'completed'
ORDER  BY b.completed_at DESC NULLS LAST
LIMIT  1;

\echo
\echo ════════ 2. What did we catch? — findings by issue type ════════
SELECT f->>'issue_type' AS issue_type,
       f->>'severity'   AS severity,
       count(*)         AS count
FROM   health_check_result r,
       jsonb_array_elements(r.result->'flagged') AS f
WHERE  r.status = 'blocked'
GROUP  BY 1, 2
ORDER  BY count DESC;

\echo
\echo ════════ 3. Document issues vs Contact issues (the split) ════════
SELECT CASE WHEN f->>'issue_type'
                 IN ('duplicate_contact','contact_defaults','inactive_contact')
            THEN 'CONTACT' ELSE 'DOCUMENT' END AS category,
       count(*) AS findings
FROM   health_check_result r,
       jsonb_array_elements(r.result->'flagged') AS f
WHERE  r.status = 'blocked'
GROUP  BY 1;

\echo
\echo ════════ 4. Duplicate invoices/bills — WITH EVIDENCE ════════
\x on
SELECT r.result->>'vendor_name'    AS contact,
       r.result->>'invoice_number' AS invoice,
       r.result->>'amount'         AS amount,
       f->>'confidence'            AS confidence,
       f->>'message'               AS finding
FROM   health_check_result r,
       jsonb_array_elements(r.result->'flagged') AS f
WHERE  f->>'issue_type' IN ('duplicate_invoice','duplicate_bill')
ORDER  BY contact;
\x off

\echo
\echo ════════ 5. Full readable findings list ════════
SELECT r.document_type                     AS doc,
       left(r.result->>'vendor_name', 24)  AS contact,
       r.result->>'amount'                 AS amount,
       left(r.result->>'messages', 80)     AS finding
FROM   health_check_result r
WHERE  r.status = 'blocked'
ORDER  BY r.document_type, contact;

\echo
\echo ════════ 6. Drill into one contact (change the name) ════════
SELECT r.result->>'invoice_number' AS invoice,
       r.result->>'amount'         AS amount,
       f->>'issue_type'            AS issue,
       f->>'message'               AS finding
FROM   health_check_result r,
       jsonb_array_elements(r.result->'flagged') AS f
WHERE  r.result->>'vendor_name' ILIKE '%Hamilton Smith%'
ORDER  BY invoice;
