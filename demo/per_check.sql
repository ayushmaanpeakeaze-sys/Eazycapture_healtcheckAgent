-- ============================================================================
--  PER-CHECK demo — one query, one health check.
--  Change ONLY the value in the WHERE line, then Execute.
--
--  Run a single check from the terminal:
--    psql "postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc" \
--      -P pager=off -x \
--      -c "SELECT r.result->>'vendor_name' AS contact,
--                 r.result->>'invoice_number' AS document,
--                 r.result->>'amount' AS amount,
--                 f->>'severity' AS severity, f->>'confidence' AS confidence,
--                 f->>'message' AS finding
--          FROM health_check_result r, jsonb_array_elements(r.result->'flagged') f
--          WHERE f->>'issue_type' = 'duplicate_invoice' ORDER BY contact;"
--
--  Or paste the query below into Adminer (http://localhost:8080) → SQL command.
--
--  Available checks (put one in the WHERE line):
--    duplicate_invoice              — duplicate sales invoices
--    wrong_direction_account        — cost booked to a fixed-asset account
--    bill_or_direct_booking         — bill with no reference (direct bank coding?)
--    invoice_or_direct_booking      — sale with no invoice number
--    multi_tax_code_supplier        — same supplier, inconsistent tax codes
--    old_unsettled_purchase_credit  — old unallocated supplier credit note
--    old_unpaid_invoice             — sales invoice unpaid too long
--    old_unpaid_bill                — bill unpaid too long
--    unapproved_invoice             — draft / not approved
--    anomaly                        — amount way off the supplier's norm
--    contact_defaults               — contact missing a default account
-- ============================================================================
\pset pager off
\x on

SELECT r.result->>'vendor_name'    AS contact,
       r.result->>'invoice_number' AS document,
       r.result->>'amount'         AS amount,
       f->>'severity'              AS severity,
       f->>'confidence'            AS confidence,
       f->>'message'               AS finding
FROM   health_check_result r,
       jsonb_array_elements(r.result->'flagged') AS f
WHERE  f->>'issue_type' = 'duplicate_invoice'   -- 👈 change this one value
ORDER  BY contact;
