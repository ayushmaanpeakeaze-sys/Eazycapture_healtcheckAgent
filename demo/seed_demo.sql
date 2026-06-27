-- ============================================================================
--  SEED DEMO DATA — fake data crafted so EVERY health check returns rows.
--  Self-contained: builds the snap_* tables (no live Xero needed).
--
--  Run:  psql "postgresql://hcpoc:hcpoc@127.0.0.1:5434/healthcheck_poc" -f demo/seed_demo.sql
--  Then: psql "...same url..." -f demo/rulebook.sql
--
--  NOTE: this is DEMO data for showing the queries — not real logic. Some
--  documents intentionally trip more than one check (realistic). Dates are
--  relative to CURRENT_DATE so the demo never goes stale.
-- ============================================================================
\pset pager off

DROP TABLE IF EXISTS snap_invoice_lines;
DROP TABLE IF EXISTS snap_invoices;
DROP TABLE IF EXISTS snap_contacts;
DROP TABLE IF EXISTS snap_accounts;
DROP TABLE IF EXISTS snap_tax_rates;

CREATE TABLE snap_accounts (code text PRIMARY KEY, name text, type text, statement text);
CREATE TABLE snap_tax_rates (code text PRIMARY KEY, name text, rate numeric,
    can_apply_to_expenses boolean, can_apply_to_revenue boolean);
CREATE TABLE snap_contacts (
    contact_id text PRIMARY KEY, name text, is_supplier boolean, is_customer boolean,
    is_archived boolean, email text, tax_number text, bank_account text, phone text,
    purchases_default_code text, sales_default_code text);
CREATE TABLE snap_invoices (
    transaction_id text PRIMARY KEY, contact_id text, vendor_name text, type text,
    status text, reference text, normalized_reference text, invoice_number text,
    amount numeric(14,2), amount_paid numeric(14,2), amount_due numeric(14,2),
    date date, due_date date, currency_code text, account_code text, tax_code text,
    description text);
CREATE TABLE snap_invoice_lines (transaction_id text, line_no int, account_code text,
    tax_code text, amount numeric(14,2), description text);

-- ---- Chart of accounts ----
INSERT INTO snap_accounts (code, name, type, statement) VALUES
 ('200','Sales','REVENUE','P&L'),
 ('400','Advertising','EXPENSE','P&L'),
 ('412','Consulting & Accounting','EXPENSE','P&L'),
 ('433','Subscriptions','EXPENSE','P&L'),
 ('449','Motor Vehicle Expenses','EXPENSE','P&L'),
 ('710','Office Equipment','FIXEDASSET','Balance Sheet'),
 ('840','Historical Adjustment','EQUITY','Balance Sheet');

-- ---- Tax rates (valid set + direction flags) ----
INSERT INTO snap_tax_rates (code, name, rate, can_apply_to_expenses, can_apply_to_revenue) VALUES
 ('INPUT2','20% (VAT on Expenses)',20,true,false),
 ('OUTPUT2','20% (VAT on Income)',20,false,true),
 ('NONE','No VAT',0,true,true),
 ('ZERORATEDOUTPUT','Zero Rated Income',0,false,true);

-- ---- Contacts (incl. 4 duplicate pairs: email / phone / bank / tax) ----
INSERT INTO snap_contacts (contact_id,name,is_supplier,is_customer,is_archived,email,tax_number,bank_account,phone,purchases_default_code,sales_default_code) VALUES
 ('c-ham','Hamilton Smith Ltd',     false,true ,false,'billing@hamiltonsmith.example',NULL,NULL,NULL,NULL,NULL),   -- customer, NO sales default
 ('c-glo','Globex Supplies Ltd',    true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-acme1','Acme Trading Ltd',     true ,false,false,'accounts@acme.example','GB-ACME','BANK-ACME','01010001','400',NULL),
 ('c-acme2','Acme Trading Limited', true ,false,false,'accounts@acme.example',NULL,NULL,NULL,'400',NULL),          -- dup EMAIL of acme1
 ('c-ini1','Initech LLC',           false,true ,false,'ap@initech.example',NULL,NULL,'02071234567',NULL,'200'),
 ('c-ini2','Initech Solutions',     false,true ,false,NULL,NULL,NULL,'02071234567',NULL,'200'),                   -- dup PHONE of ini1
 ('c-way1','Wayne Enterprises',     true ,false,false,'pay@wayne.example',NULL,'GB29NWBK60161331926819','01010002','400',NULL),
 ('c-way2','Wayne Enterprise',      true ,false,false,NULL,NULL,'GB29NWBK60161331926819',NULL,'400',NULL),        -- dup BANK of way1
 ('c-umb1','Umbrella Corp',         true ,false,false,'ar@umbrella.example','GB123456789',NULL,'01010003','400',NULL),
 ('c-umb2','Umbrella Corporation',  true ,false,false,NULL,'GB123456789',NULL,NULL,'400',NULL),                   -- dup TAX of umb1
 ('c-stark','Stark Industries',     true ,false,false,'ap@stark.example',NULL,NULL,NULL,NULL,NULL),               -- supplier, NO purchase default
 ('c-sleepy','Sleepy Vendor Ltd',   true ,true ,false,NULL,NULL,NULL,NULL,'400','200'),                           -- NO documents -> inactive
 ('c-ca','Vector Dynamics Ltd',     true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-cb','Pied Piper Ltd',          false,true ,false,NULL,NULL,NULL,NULL,NULL,'200'),
 ('c-cc','Hooli Inc',               true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-cd','Aperture Labs',           true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-ce','Black Mesa Ltd',          true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-cf','Tyrell Corp',             true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-cg','Soylent Corp',            false,true ,false,NULL,NULL,NULL,NULL,NULL,'200'),
 ('c-ch','Oscorp Ltd',              true ,false,false,NULL,NULL,NULL,NULL,'400',NULL),
 ('c-ci','Gekko & Co',              true ,false,false,NULL,NULL,NULL,NULL,'400',NULL);

-- ---- Invoices (col order: tid,contact,vendor,type,status,ref,invno,amount,paid,due,date,due_date,ccy,acct,tax,desc) ----
INSERT INTO snap_invoices (transaction_id,contact_id,vendor_name,type,status,reference,invoice_number,amount,amount_paid,amount_due,date,due_date,currency_code,account_code,tax_code,description) VALUES
-- duplicate_invoice (Hamilton): INV-0001 & INV-0005 (1 day apart); INV-0018 is recurring (30d) and must NOT flag
 ('t-d1','c-ham','Hamilton Smith Ltd','ACCREC','PAID','Monthly Support','INV-0001',541.25,541.25,0,CURRENT_DATE-80,CURRENT_DATE-80,'GBP','200','OUTPUT2','Monthly support retainer'),
 ('t-d2','c-ham','Hamilton Smith Ltd','ACCREC','PAID','Monthly Support','INV-0005',541.25,541.25,0,CURRENT_DATE-79,CURRENT_DATE-79,'GBP','200','OUTPUT2','Monthly support retainer'),
 ('t-d3','c-ham','Hamilton Smith Ltd','ACCREC','PAID','Monthly Support','INV-0018',541.25,541.25,0,CURRENT_DATE-49,CURRENT_DATE-49,'GBP','200','OUTPUT2','Monthly support retainer'),
-- invoice_or_direct_booking (ACCREC authorised, no number)
 ('t-id1','c-ham','Hamilton Smith Ltd','ACCREC','AUTHORISED',NULL,NULL,480,0,480,CURRENT_DATE-12,CURRENT_DATE+18,'GBP','200','OUTPUT2','Ad-hoc receipt'),
-- unapproved_invoice (DRAFT > 7 days, has number)
 ('t-un1','c-ham','Hamilton Smith Ltd','ACCREC','DRAFT','Retainer','INV-DR1',550,0,550,CURRENT_DATE-20,CURRENT_DATE+10,'GBP','200','OUTPUT2','Draft retainer'),
-- old_unsettled_sales_credit (>60d, outstanding>0)
 ('t-cr2','c-ham','Hamilton Smith Ltd','ACCRECCREDIT','PAID',NULL,'CN-200',200,0,200,CURRENT_DATE-90,NULL,'GBP','200','OUTPUT2','Sales credit note'),
-- duplicate_bill (Globex): SUP-778 twice (2 days apart)
 ('t-b1','c-glo','Globex Supplies Ltd','ACCPAY','AUTHORISED','SUP-778','BILL-100',580,0,580,CURRENT_DATE-40,CURRENT_DATE-10,'GBP','400','INPUT2','Marketing services'),
 ('t-b2','c-glo','Globex Supplies Ltd','ACCPAY','AUTHORISED','SUP-778','BILL-101',580,0,580,CURRENT_DATE-38,CURRENT_DATE-8,'GBP','400','INPUT2','Marketing services'),
-- bill_or_direct_booking (ACCPAY authorised, no number)
 ('t-bd1','c-glo','Globex Supplies Ltd','ACCPAY','AUTHORISED',NULL,NULL,250,0,250,CURRENT_DATE-12,CURRENT_DATE+18,'GBP','400','INPUT2','Direct bank spend'),
-- unapproved_bill (SUBMITTED > 7 days)
 ('t-un2','c-glo','Globex Supplies Ltd','ACCPAY','SUBMITTED','SUP-DR1','BILL-DR1',300,0,300,CURRENT_DATE-20,CURRENT_DATE+10,'GBP','400','INPUT2','Submitted bill'),
-- old_unsettled_purchase_credit (>60d)
 ('t-cr1','c-glo','Globex Supplies Ltd','ACCPAYCREDIT','PAID',NULL,'CN-100',270.63,0,270.63,CURRENT_DATE-90,NULL,'GBP','400','INPUT2','Purchase credit note'),
-- multi_account_supplier (Acme1: 400 x3 + 412 x1)
 ('t-ma1','c-acme1','Acme Trading Ltd','ACCPAY','AUTHORISED','A-1','BILL-A1',100,0,0,CURRENT_DATE-30,CURRENT_DATE-1,'GBP','400','INPUT2','Supplies'),
 ('t-ma2','c-acme1','Acme Trading Ltd','ACCPAY','AUTHORISED','A-2','BILL-A2',110,0,0,CURRENT_DATE-28,CURRENT_DATE-1,'GBP','400','INPUT2','Supplies'),
 ('t-ma3','c-acme1','Acme Trading Ltd','ACCPAY','AUTHORISED','A-3','BILL-A3',120,0,0,CURRENT_DATE-26,CURRENT_DATE-1,'GBP','400','INPUT2','Supplies'),
 ('t-ma4','c-acme1','Acme Trading Ltd','ACCPAY','AUTHORISED','A-4','BILL-A4',130,0,0,CURRENT_DATE-24,CURRENT_DATE-1,'GBP','412','INPUT2','Supplies (odd account)'),
-- multi_tax_code_supplier (Wayne1: INPUT2 x3 + NONE x1)
 ('t-mt1','c-way1','Wayne Enterprises','ACCPAY','AUTHORISED','W-1','BILL-W1',200,0,0,CURRENT_DATE-30,CURRENT_DATE-1,'GBP','400','INPUT2','Logistics'),
 ('t-mt2','c-way1','Wayne Enterprises','ACCPAY','AUTHORISED','W-2','BILL-W2',210,0,0,CURRENT_DATE-28,CURRENT_DATE-1,'GBP','400','INPUT2','Logistics'),
 ('t-mt3','c-way1','Wayne Enterprises','ACCPAY','AUTHORISED','W-3','BILL-W3',220,0,0,CURRENT_DATE-26,CURRENT_DATE-1,'GBP','400','INPUT2','Logistics'),
 ('t-mt4','c-way1','Wayne Enterprises','ACCPAY','AUTHORISED','W-4','BILL-W4',230,0,0,CURRENT_DATE-24,CURRENT_DATE-1,'GBP','400','NONE','Logistics (odd tax)'),
-- amount_outlier / anomaly (Umbrella1: 100/110/120/130 then 5000)
 ('t-an1','c-umb1','Umbrella Corp','ACCPAY','AUTHORISED','U-1','BILL-U1',100,0,0,CURRENT_DATE-30,CURRENT_DATE-1,'GBP','400','INPUT2','Cleaning'),
 ('t-an2','c-umb1','Umbrella Corp','ACCPAY','AUTHORISED','U-2','BILL-U2',110,0,0,CURRENT_DATE-28,CURRENT_DATE-1,'GBP','400','INPUT2','Cleaning'),
 ('t-an3','c-umb1','Umbrella Corp','ACCPAY','AUTHORISED','U-3','BILL-U3',120,0,0,CURRENT_DATE-26,CURRENT_DATE-1,'GBP','400','INPUT2','Cleaning'),
 ('t-an4','c-umb1','Umbrella Corp','ACCPAY','AUTHORISED','U-4','BILL-U4',130,0,0,CURRENT_DATE-24,CURRENT_DATE-1,'GBP','400','INPUT2','Cleaning'),
 ('t-an5','c-umb1','Umbrella Corp','ACCPAY','AUTHORISED','U-5','BILL-U5',5000,0,0,CURRENT_DATE-22,CURRENT_DATE-1,'GBP','400','INPUT2','Cleaning (huge)'),
-- duplicate-partner contacts: 1 clean doc each so they show only in duplicate_contact
 ('t-cl1','c-acme2','Acme Trading Limited','ACCPAY','PAID','CL-1','BILL-CL1',90,90,0,CURRENT_DATE-10,CURRENT_DATE-10,'GBP','400','INPUT2','Supplies'),
 ('t-cl2','c-ini2','Initech Solutions','ACCREC','PAID','CL-2','INV-CL2',90,90,0,CURRENT_DATE-10,CURRENT_DATE-10,'GBP','200','OUTPUT2','Services'),
 ('t-cl3','c-way2','Wayne Enterprise','ACCPAY','PAID','CL-3','BILL-CL3',95,95,0,CURRENT_DATE-10,CURRENT_DATE-10,'GBP','400','INPUT2','Logistics'),
 ('t-cl4','c-umb2','Umbrella Corporation','ACCPAY','PAID','CL-4','BILL-CL4',96,96,0,CURRENT_DATE-10,CURRENT_DATE-10,'GBP','400','INPUT2','Cleaning'),
-- contact_defaults: Stark (supplier, no purchase default) needs 1 doc so it is not 'inactive' too
 ('t-cl5','c-stark','Stark Industries','ACCPAY','PAID','CL-5','BILL-CL5',99,99,0,CURRENT_DATE-10,CURRENT_DATE-10,'GBP','400','INPUT2','Consulting'),
-- wrong_direction_account (ACCPAY -> REVENUE 200) + old_unpaid_bill
 ('t-wd1','c-ca','Vector Dynamics Ltd','ACCPAY','AUTHORISED','V-1','BILL-V1',800,0,800,CURRENT_DATE-30,CURRENT_DATE-5,'GBP','200','INPUT2','Bill on sales account'),
 ('t-ou1','c-ca','Vector Dynamics Ltd','ACCPAY','AUTHORISED','V-2','BILL-V2',1063.56,0,1063.56,CURRENT_DATE-95,CURRENT_DATE-75,'GBP','400','INPUT2','Overdue bill'),
-- wrong_direction_account (ACCREC -> EXPENSE 400) + old_unpaid_invoice
 ('t-wd2','c-cb','Pied Piper Ltd','ACCREC','AUTHORISED','P-1','INV-P1',300,0,300,CURRENT_DATE-30,CURRENT_DATE-5,'GBP','400','OUTPUT2','Sale on expense account'),
 ('t-ou2','c-cb','Pied Piper Ltd','ACCREC','AUTHORISED','P-2','INV-P2',250,0,250,CURRENT_DATE-95,CURRENT_DATE-75,'GBP','200','OUTPUT2','Overdue invoice'),
-- future_dated + invalid_status_combo
 ('t-fd1','c-cc','Hooli Inc','ACCPAY','AUTHORISED','H-1','BILL-H1',500,0,0,CURRENT_DATE+15,CURRENT_DATE+45,'GBP','400','INPUT2','Future dated'),
 ('t-sc1','c-cc','Hooli Inc','ACCPAY','PAID','H-2','BILL-H2',500,350,150,CURRENT_DATE-30,CURRENT_DATE-15,'GBP','400','INPUT2','Marked paid but owing'),
-- missing_invoice_number (DRAFT, no number) + unexpected_account (449 used once)
 ('t-mn1','c-cd','Aperture Labs','ACCPAY','DRAFT',NULL,NULL,100,0,0,CURRENT_DATE-3,CURRENT_DATE+27,'GBP','400','INPUT2','Draft, no number'),
 ('t-ua1','c-cd','Aperture Labs','ACCPAY','AUTHORISED','AP-2','BILL-AP2',200,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','449','INPUT2','Rare account'),
-- opening_balance_difference (account 840) + unexpected_tax_code (ZERORATEDOUTPUT used once)
 ('t-ob1','c-ce','Black Mesa Ltd','ACCPAY','PAID','BM-1','BILL-BM1',1500,1500,0,CURRENT_DATE-200,NULL,'GBP','840','NONE','Opening balance adj'),
 ('t-ut1','c-ce','Black Mesa Ltd','ACCREC','AUTHORISED','BM-2','INV-BM2',300,0,300,CURRENT_DATE-10,CURRENT_DATE+20,'GBP','200','ZERORATEDOUTPUT','Zero-rated sale'),
-- purchase_tax_missing (no tax) + sales_tax_on_bills (OUTPUT2 on a bill)
 ('t-pt1','c-cf','Tyrell Corp','ACCPAY','AUTHORISED','T-1','BILL-T1',400,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','400',NULL,'Bill with no tax code'),
 ('t-tx1','c-cf','Tyrell Corp','ACCPAY','AUTHORISED','T-2','BILL-T2',600,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','400','OUTPUT2','Bill with output VAT'),
-- sales_tax_missing (no tax) + purchase_tax_on_invoices (INPUT2 on a sale)
 ('t-st1','c-cg','Soylent Corp','ACCREC','AUTHORISED','S-1','INV-S1',300,0,300,CURRENT_DATE-10,CURRENT_DATE+20,'GBP','200',NULL,'Sale with no tax code'),
 ('t-tx2','c-cg','Soylent Corp','ACCREC','AUTHORISED','S-2','INV-S2',600,0,600,CURRENT_DATE-10,CURRENT_DATE+20,'GBP','200','INPUT2','Sale with input VAT'),
-- invalid_tax_code (TAX999) + wrong_category (subscription on Consulting)
 ('t-it1','c-ch','Oscorp Ltd','ACCPAY','AUTHORISED','O-1','BILL-O1',200,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','400','TAX999','Unknown tax code'),
 ('t-wc1','c-ch','Oscorp Ltd','ACCPAY','AUTHORISED','O-2','BILL-O2',30,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','412','INPUT2','Xero monthly subscription'),
-- capital_item_review (laptop on expense) + low_cost_fixed_asset (keyboard on fixed asset)
 ('t-cap1','c-ci','Gekko & Co','ACCPAY','AUTHORISED','G-1','BILL-G1',1200,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','400','INPUT2','Dell laptop computer'),
 ('t-lc1','c-ci','Gekko & Co','ACCPAY','AUTHORISED','G-2','BILL-G2',80,0,0,CURRENT_DATE-12,CURRENT_DATE-1,'GBP','710','INPUT2','USB keyboard'),
-- missing_vendor (no vendor name)
 ('t-mv1',NULL,'','ACCPAY','AUTHORISED','MV-1','BILL-MV1',100,0,0,CURRENT_DATE-5,CURRENT_DATE+25,'GBP','400','INPUT2','No vendor');

-- compute normalized_reference exactly like the engine's _normalize_ref
UPDATE snap_invoices
   SET normalized_reference =
       nullif(lower(regexp_replace(coalesce(reference,''), '[^a-z0-9]', '', 'gi')), '');

-- one line per invoice mirroring the header (so snap_invoice_lines is consistent)
INSERT INTO snap_invoice_lines (transaction_id, line_no, account_code, tax_code, amount, description)
SELECT transaction_id, 1, account_code, tax_code, amount, description FROM snap_invoices;

\echo 'Seed loaded:'
SELECT (SELECT count(*) FROM snap_invoices) AS invoices,
       (SELECT count(*) FROM snap_contacts) AS contacts,
       (SELECT count(*) FROM snap_accounts) AS accounts,
       (SELECT count(*) FROM snap_tax_rates) AS tax_rates;
