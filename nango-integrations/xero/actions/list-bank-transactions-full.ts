import { createAction } from 'nango';
import * as z from 'zod';

/**
 * list-bank-transactions-full — one page of Xero bank transactions (Money In /
 * Money Out) WITH full line items + the IsReconciled flag.
 *
 * Why this exists: Nango's pre-built `list-bank-transactions` returns NO line
 * items (only a lineItemCount + the bank account). Our checks need each line's
 * AccountCode + TaxType + amount (sales/purchase tax-missing, tax-on-bills,
 * misallocated, capital — these run on SPEND/RECEIVE too) and `IsReconciled`
 * (Unreconciled Bank Items). This calls Xero's GET /BankTransactions straight
 * through and returns the raw `BankTransactions` array — lines intact — one page
 * (100) at a time so each response stays under the action output cap.
 *
 * Pagination: pass `page` (1-based); loop until an empty page. Both RECEIVE
 * (Money In) and SPEND (Money Out) come from this same endpoint.
 */
export default createAction({
    description: 'List one page of Xero bank transactions with full line items (AccountCode, tax, amounts) + IsReconciled.',
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        page: z.number().int().positive().optional(),
        // Optional Xero filter, e.g. 'Type=="SPEND"' or 'IsReconciled==false'.
        where: z.string().optional(),
        // ISO timestamp → only txns changed since then (Xero If-Modified-Since).
        modifiedSince: z.string().optional(),
    }),
    output: z.object({
        page: z.number(),
        count: z.number(),
        bankTransactions: z.array(z.record(z.string(), z.unknown())),
    }),
    exec: async (nango, input) => {
        const page = input.page ?? 1;

        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;
        if (input.modifiedSince) headers['If-Modified-Since'] = input.modifiedSince;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/BankTransactions',
            params: {
                page,
                ...(input.where ? { where: input.where } : {}),
            },
            headers,
        });

        const bankTransactions = (res.data?.BankTransactions ?? []) as Array<Record<string, unknown>>;
        return { page, count: bankTransactions.length, bankTransactions };
    },
});
