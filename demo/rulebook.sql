-- ============================================================================
--  RULEBOOK — every health check as a runnable SQL query (evidence style).
--  Each query shows the proof columns + a why_flagged note explaining the flag.
--
--  PREREQ:  psql "postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc" -f demo/seed_demo.sql
--  RUN ALL: psql "postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc" -f demo/rulebook.sql
--  Or paste any single block into Adminer (http://localhost:8080).
-- ============================================================================
\pset pager off

\echo '======== 1. DUPLICATE INVOICES / BILLS ========'
SELECT a.vendor_name AS contact, a.invoice_number AS doc_a, b.invoice_number AS doc_b,
       a.amount, a.reference, a.date AS date_a, b.date AS date_b,
       'same ref + amount, '||abs(a.date-b.date)||' day(s) apart' AS why_flagged
FROM snap_invoices a JOIN snap_invoices b
  ON a.contact_id=b.contact_id AND a.normalized_reference=b.normalized_reference
 AND a.amount=b.amount AND a.type=b.type AND a.transaction_id<b.transaction_id
WHERE a.status NOT IN ('VOIDED','DELETED') AND coalesce(a.normalized_reference,'')<>''
  AND abs(a.date-b.date)<=7 ORDER BY contact;

\echo '======== 2. WRONG-DIRECTION ACCOUNT ========'
SELECT i.vendor_name AS contact, i.type AS doc_type, i.invoice_number AS doc, i.amount,
       i.account_code, acc.name AS account, acc.type AS account_type,
       CASE WHEN i.type IN ('ACCREC','ACCRECCREDIT') THEN 'sale -> should be REVENUE'
            ELSE 'purchase -> should be EXPENSE/asset' END AS why_flagged
FROM snap_invoices i JOIN snap_accounts acc ON acc.code=i.account_code
WHERE (i.type IN ('ACCREC','ACCRECCREDIT') AND acc.type NOT IN ('REVENUE','OTHERINCOME','SALES'))
   OR (i.type IN ('ACCPAY','ACCPAYCREDIT') AND acc.type NOT IN
        ('EXPENSE','DIRECTCOSTS','OVERHEADS','DEPRECIATN','CURRENTASSET','FIXEDASSET',
         'INVENTORY','PREPAYMENT','ASSET','LIABILITY')) ORDER BY contact;

\echo '======== 3. AUTHORISED DOC WITH NO INVOICE/BILL NUMBER ========'
SELECT vendor_name AS contact, type AS doc_type, amount, date, status,
       'authorised but invoice/bill number is blank' AS why_flagged,
       CASE WHEN type IN ('ACCREC','ACCRECCREDIT') THEN 'invoice_or_direct_booking'
            ELSE 'bill_or_direct_booking' END AS check_type
FROM snap_invoices
WHERE coalesce(invoice_number,'')='' AND status IN ('AUTHORISED','PAID')
  AND type IN ('ACCREC','ACCRECCREDIT','ACCPAY','ACCPAYCREDIT') ORDER BY check_type, contact;

\echo '======== 4. MISSING INVOICE NUMBER (draft / not authorised) ========'
SELECT vendor_name AS contact, type AS doc_type, amount, status, date,
       'no invoice number on a '||status||' doc' AS why_flagged
FROM snap_invoices WHERE coalesce(invoice_number,'')='' AND status NOT IN ('AUTHORISED','PAID') ORDER BY contact;

\echo '======== 5. MISSING VENDOR ========'
SELECT transaction_id AS doc, type AS doc_type, amount, date,
       'vendor/contact name is blank' AS why_flagged
FROM snap_invoices WHERE coalesce(vendor_name,'')='' ORDER BY date;

\echo '======== 6. FUTURE-DATED DOCUMENT ========'
SELECT vendor_name AS contact, type AS doc_type, invoice_number AS doc, amount, date,
       'dated '||(date - CURRENT_DATE)||' day(s) in the future' AS why_flagged
FROM snap_invoices WHERE date > CURRENT_DATE ORDER BY date;

\echo '======== 7. INVALID STATUS COMBO (PAID but still owing) ========'
SELECT vendor_name AS contact, invoice_number AS doc, amount, amount_paid, amount_due, status,
       'status PAID but '||amount_due||' still outstanding' AS why_flagged
FROM snap_invoices WHERE upper(status)='PAID' AND coalesce(amount_due,0)>0 ORDER BY contact;

\echo '======== 8. OLD UNPAID BILL / INVOICE (> 60 days) ========'
SELECT vendor_name AS contact, type AS doc_type, invoice_number AS doc, amount, amount_due,
       (CURRENT_DATE - coalesce(due_date,date)) AS days_overdue,
       amount_due||' unpaid for '||(CURRENT_DATE - coalesce(due_date,date))||' days' AS why_flagged
FROM snap_invoices
WHERE coalesce(amount_due,0)>0 AND upper(status)<>'PAID' AND type IN ('ACCREC','ACCPAY')
  AND (CURRENT_DATE - coalesce(due_date,date)) >= 60 ORDER BY days_overdue DESC;

\echo '======== 9. UNAPPROVED (DRAFT/SUBMITTED) > 7 days ========'
SELECT vendor_name AS contact, type AS doc_type, amount, status,
       (CURRENT_DATE-date) AS age_days,
       status||' for '||(CURRENT_DATE-date)||' days - needs approval' AS why_flagged
FROM snap_invoices WHERE upper(status) IN ('DRAFT','SUBMITTED') AND (CURRENT_DATE-date)>7 ORDER BY age_days DESC;

\echo '======== 10. OLD UNSETTLED CREDIT NOTES (> 60 days) ========'
SELECT vendor_name AS contact, type AS doc_type, amount,
       coalesce(amount_due, amount - coalesce(amount_paid,0)) AS outstanding,
       (CURRENT_DATE-date) AS age_days,
       coalesce(amount_due, amount-coalesce(amount_paid,0))||' unapplied for '||(CURRENT_DATE-date)||' days' AS why_flagged
FROM snap_invoices
WHERE type IN ('ACCPAYCREDIT','ACCRECCREDIT')
  AND coalesce(amount_due, amount - coalesce(amount_paid,0)) > 0 AND (CURRENT_DATE-date)>60 ORDER BY age_days DESC;

\echo '======== 11. OPENING BALANCE / HISTORICAL ADJUSTMENT ========'
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount, i.account_code, acc.name AS account,
       'posted to historical/opening-balance account '||i.account_code AS why_flagged
FROM snap_invoices i JOIN snap_accounts acc ON acc.code=i.account_code
WHERE i.account_code='840' OR acc.name ILIKE '%historical adjustment%' OR acc.name ILIKE '%opening balance%' ORDER BY contact;

\echo '======== 12. MULTI-ACCOUNT SUPPLIER ========'
WITH base AS (
  SELECT contact_id, mode() WITHIN GROUP (ORDER BY account_code) AS usual_account
  FROM snap_invoices WHERE coalesce(account_code,'')<>''
  GROUP BY contact_id HAVING count(*)>=3 AND count(DISTINCT account_code)>=2)
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount,
       i.account_code AS this_account, b.usual_account,
       'usually posts to '||b.usual_account||', this one is '||i.account_code AS why_flagged
FROM snap_invoices i JOIN base b ON b.contact_id=i.contact_id
WHERE i.account_code <> b.usual_account ORDER BY contact;

\echo '======== 13. MULTI-TAX-CODE SUPPLIER ========'
WITH base AS (
  SELECT contact_id, mode() WITHIN GROUP (ORDER BY upper(tax_code)) AS usual_tax
  FROM snap_invoices WHERE type IN ('ACCPAY','ACCPAYCREDIT') AND coalesce(tax_code,'')<>''
  GROUP BY contact_id HAVING count(*)>=3 AND count(DISTINCT upper(tax_code))>=2)
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount,
       i.tax_code AS this_tax, b.usual_tax,
       'usually uses '||b.usual_tax||', this one is '||i.tax_code AS why_flagged
FROM snap_invoices i JOIN base b ON b.contact_id=i.contact_id
WHERE i.type IN ('ACCPAY','ACCPAYCREDIT') AND coalesce(i.tax_code,'')<>'' AND upper(i.tax_code)<>b.usual_tax ORDER BY contact;

\echo '======== 14. AMOUNT ANOMALY (candidates; AI confirms) ========'
WITH base AS (
  SELECT contact_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) AS median_amount
  FROM snap_invoices WHERE amount>0 GROUP BY contact_id HAVING count(*)>=4)
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount,
       round(b.median_amount::numeric,2) AS contact_median,
       round((i.amount/nullif(b.median_amount,0))::numeric,1) AS x_times_median,
       round((i.amount/nullif(b.median_amount,0))::numeric,1)||'x the contact''s usual amount' AS why_flagged
FROM snap_invoices i JOIN base b ON b.contact_id=i.contact_id
WHERE i.amount >= b.median_amount*4 AND i.amount >= 100 ORDER BY x_times_median DESC;

\echo '======== 15. PURCHASE TAX MISSING ========'
SELECT vendor_name AS contact, invoice_number AS doc, amount, account_code,
       'bill has no tax code' AS why_flagged
FROM snap_invoices WHERE type IN ('ACCPAY','ACCPAYCREDIT') AND coalesce(tax_code,'')='' ORDER BY contact;

\echo '======== 16. SALES TAX MISSING ========'
SELECT vendor_name AS contact, invoice_number AS doc, amount, account_code,
       'invoice has no tax code' AS why_flagged
FROM snap_invoices WHERE type IN ('ACCREC','ACCRECCREDIT') AND coalesce(tax_code,'')='' ORDER BY contact;

\echo '======== 17. SALES TAX ON A BILL (output VAT on a purchase) ========'
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount, i.tax_code, t.name AS tax_name,
       'output/sales VAT used on a bill (cannot apply to expenses)' AS why_flagged
FROM snap_invoices i JOIN snap_tax_rates t ON t.code=i.tax_code
WHERE i.type IN ('ACCPAY','ACCPAYCREDIT') AND t.can_apply_to_expenses=false ORDER BY contact;

\echo '======== 18. PURCHASE TAX ON AN INVOICE (input VAT on a sale) ========'
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount, i.tax_code, t.name AS tax_name,
       'input/purchase VAT used on a sale (cannot apply to revenue)' AS why_flagged
FROM snap_invoices i JOIN snap_tax_rates t ON t.code=i.tax_code
WHERE i.type IN ('ACCREC','ACCRECCREDIT') AND t.can_apply_to_revenue=false ORDER BY contact;

\echo '======== 19. INVALID TAX CODE ========'
SELECT vendor_name AS contact, invoice_number AS doc, amount, tax_code,
       'tax code '||tax_code||' is not in this org''s Xero' AS why_flagged
FROM snap_invoices WHERE coalesce(tax_code,'')<>'' AND tax_code NOT IN (SELECT code FROM snap_tax_rates) ORDER BY contact;

\echo '======== 20. UNEXPECTED TAX CODE (used only once) ========'
WITH c AS (SELECT upper(tax_code) tc, count(*) n FROM snap_invoices WHERE coalesce(tax_code,'')<>'' GROUP BY 1)
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.tax_code,
       'tax code used only once in the whole set' AS why_flagged
FROM snap_invoices i JOIN c ON c.tc=upper(i.tax_code) WHERE c.n=1 ORDER BY contact;

\echo '======== 21. UNEXPECTED ACCOUNT (used only once) ========'
WITH c AS (SELECT account_code ac, count(*) n FROM snap_invoices WHERE coalesce(account_code,'')<>'' GROUP BY 1)
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.account_code,
       'account used only once in the whole set' AS why_flagged
FROM snap_invoices i JOIN c ON c.ac=i.account_code WHERE c.n=1 ORDER BY contact;

\echo '======== 22. WRONG CATEGORY (candidate; AI confirms) ========'
SELECT vendor_name AS contact, invoice_number AS doc, amount, account_code, description,
       'description says "subscription" but coded to '||account_code||' (not 433 Subscriptions)' AS why_flagged
FROM snap_invoices WHERE description ILIKE '%subscription%' AND account_code <> '433' ORDER BY contact;

\echo '======== 23. CAPITAL ITEM REVIEW (candidate; AI confirms) ========'
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount, i.account_code, i.description,
       'high-value asset-like item ('||i.amount||') booked to expense '||i.account_code AS why_flagged
FROM snap_invoices i JOIN snap_accounts a ON a.code=i.account_code
WHERE a.type IN ('EXPENSE','DIRECTCOSTS','OVERHEADS') AND i.amount >= 500
  AND i.description ~* '(laptop|computer|equipment|furniture|machine|server|vehicle)' ORDER BY contact;

\echo '======== 24. LOW-COST FIXED ASSET (candidate; AI confirms) ========'
SELECT i.vendor_name AS contact, i.invoice_number AS doc, i.amount, i.account_code, i.description,
       'low-value item ('||i.amount||') booked to fixed-asset '||i.account_code AS why_flagged
FROM snap_invoices i JOIN snap_accounts a ON a.code=i.account_code
WHERE a.type='FIXEDASSET' AND i.amount < 500 ORDER BY contact;

\echo '======== 25. CONTACT DEFAULTS MISSING ========'
SELECT name AS contact, is_supplier, is_customer,
       trim(BOTH ', ' FROM concat_ws(', ',
         CASE WHEN is_supplier AND coalesce(purchases_default_code,'')='' THEN 'purchase default' END,
         CASE WHEN is_customer AND coalesce(sales_default_code,'')='' THEN 'sales default' END))||' not set' AS why_flagged
FROM snap_contacts
WHERE is_archived=false
  AND ((is_supplier AND coalesce(purchases_default_code,'')='')
    OR (is_customer AND coalesce(sales_default_code,'')='')) ORDER BY contact;

\echo '======== 26. INACTIVE CONTACT (no documents) ========'
SELECT c.name AS contact, c.is_supplier, c.is_customer,
       'supplier/customer with no documents in the set' AS why_flagged
FROM snap_contacts c
WHERE c.is_archived=false AND (c.is_supplier OR c.is_customer)
  AND NOT EXISTS (SELECT 1 FROM snap_invoices i WHERE i.contact_id=c.contact_id) ORDER BY contact;

\echo '======== 27. DUPLICATE CONTACTS (shows what matched / what did not) ========'
SELECT a.name AS contact_a, b.name AS contact_b,
  CASE WHEN coalesce(a.email,'')<>''        AND a.email=b.email               THEN a.email        ELSE '-' END AS email_match,
  CASE WHEN coalesce(a.phone,'')<>''        AND a.phone=b.phone               THEN a.phone        ELSE '-' END AS phone_match,
  CASE WHEN coalesce(a.bank_account,'')<>'' AND a.bank_account=b.bank_account THEN a.bank_account ELSE '-' END AS bank_match,
  CASE WHEN coalesce(a.tax_number,'')<>''   AND a.tax_number=b.tax_number     THEN a.tax_number   ELSE '-' END AS tax_match
FROM snap_contacts a JOIN snap_contacts b ON a.contact_id<b.contact_id
WHERE a.is_archived=false AND b.is_archived=false
  AND ((coalesce(a.tax_number,'')<>''   AND a.tax_number=b.tax_number)
    OR (coalesce(a.bank_account,'')<>'' AND a.bank_account=b.bank_account)
    OR (coalesce(a.email,'')<>''        AND a.email=b.email)
    OR (coalesce(a.phone,'')<>''        AND a.phone=b.phone)) ORDER BY contact_a;
