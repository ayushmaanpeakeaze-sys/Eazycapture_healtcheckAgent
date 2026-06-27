import { createAction } from 'nango';
import * as z from 'zod';

/**
 * list-invoices-full — one page of Xero invoices/bills WITH full line items.
 *
 * Why this exists: Nango's pre-built `list-invoices` action returns invoices
 * with EMPTY LineItems (it stays under the 2 MB action-output cap by omitting
 * them). Our bookkeeping checks (low-cost fixed asset, capital item, unexpected
 * account, tax) need each line's AccountCode + amount. This action calls Xero's
 * GET /Invoices straight through and returns the raw `Invoices` array — line
 * items intact — one page (100 docs) at a time so each response stays small.
 *
 * Pagination: pass `page` (1-based). The caller loops pages until an empty one.
 * Both ACCREC (invoices) and ACCPAY (bills) come from this same endpoint.
 */
export default createAction({
    description: 'List one page of Xero invoices/bills with full line items (AccountCode, amounts, tax).',
    version: '1.0.0',
    input: z.object({
        page: z.number().int().positive().optional(),
        // Optional Xero filter, e.g. 'Type=="ACCPAY"' or 'Status=="AUTHORISED"'.
        where: z.string().optional(),
        // ISO timestamp → only invoices changed since then (Xero If-Modified-Since).
        modifiedSince: z.string().optional(),
    }),
    output: z.object({
        page: z.number(),
        count: z.number(),
        invoices: z.array(z.record(z.string(), z.unknown())),
    }),
    exec: async (nango, input) => {
        const page = input.page ?? 1;

        // Xero requires the tenant id on every call. Inside a function we must
        // pass it ourselves — read it from the connection metadata (set once via
        // POST /connection/{id}/metadata as { tenant_id }).
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = meta?.tenant_id;

        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;
        if (input.modifiedSince) headers['If-Modified-Since'] = input.modifiedSince;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/Invoices',
            params: {
                page,
                ...(input.where ? { where: input.where } : {}),
            },
            headers,
        });

        const invoices = (res.data?.Invoices ?? []) as Array<Record<string, unknown>>;
        return { page, count: invoices.length, invoices };
    },
});
