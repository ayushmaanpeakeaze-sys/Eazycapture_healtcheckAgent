import { createAction } from 'nango';
import * as z from 'zod';

/**
 * list-credit-notes-full — one page of Xero credit notes WITH full line items,
 * RemainingCredit, and allocations.
 *
 * Why this exists: even if a pre-built credit-notes list is available, our checks
 * need the per-line AccountCode + TaxType (tax/coding checks run on credit notes
 * too), the `RemainingCredit` field (Old Unsettled Credits — a fully-allocated
 * credit reads 0, an open one reads its balance), and `Allocations`
 * (duplicate-credit + settlement logic). This calls Xero's GET /CreditNotes
 * straight through and returns the raw `CreditNotes` array — lines + allocations
 * intact — one page (100) at a time.
 *
 * Pagination: pass `page` (1-based); loop until an empty page. Both ACCRECCREDIT
 * (customer) and ACCPAYCREDIT (supplier) come from this same endpoint.
 */
export default createAction({
    description: 'List one page of Xero credit notes with full line items, RemainingCredit and allocations.',
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        page: z.number().int().positive().optional(),
        // Optional Xero filter, e.g. 'Type=="ACCPAYCREDIT"' or 'Status=="AUTHORISED"'.
        where: z.string().optional(),
        // ISO timestamp → only credit notes changed since then (If-Modified-Since).
        modifiedSince: z.string().optional(),
    }),
    output: z.object({
        page: z.number(),
        count: z.number(),
        creditNotes: z.array(z.record(z.string(), z.unknown())),
    }),
    exec: async (nango, input) => {
        const page = input.page ?? 1;

        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;
        if (input.modifiedSince) headers['If-Modified-Since'] = input.modifiedSince;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/CreditNotes',
            params: {
                page,
                ...(input.where ? { where: input.where } : {}),
            },
            headers,
        });

        const creditNotes = (res.data?.CreditNotes ?? []) as Array<Record<string, unknown>>;
        return { page, count: creditNotes.length, creditNotes };
    },
});
