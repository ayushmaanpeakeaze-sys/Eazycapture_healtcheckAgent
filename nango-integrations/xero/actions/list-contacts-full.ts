import { createAction } from 'nango';
import * as z from 'zod';

/**
 * list-contacts-full — one page of Xero contacts WITH defaults + email + status.
 *
 * Why this exists: Nango's pre-built `list-contacts` returns only `{ id, name }`.
 * Our checks need each contact's DEFAULTS — SalesDefaultAccountCode,
 * PurchasesDefaultAccountCode, AccountsReceivableTaxType (sales tax),
 * AccountsPayableTaxType (purchase tax) — plus EmailAddress and ContactStatus
 * (ACTIVE / ARCHIVED). These power the default-based Unexpected-Account /
 * Unexpected-Tax checks, Contact-Defaults-missing, and Inactive-Contacts.
 * Calls Xero's GET /Contacts straight through, raw `Contacts` array, one page
 * (100) at a time.
 */
export default createAction({
    description: 'List one page of Xero contacts with defaults (sales/purchase account+tax), email and status.',
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        page: z.number().int().positive().optional(),
        where: z.string().optional(),
        modifiedSince: z.string().optional(),
    }),
    output: z.object({
        page: z.number(),
        count: z.number(),
        contacts: z.array(z.record(z.string(), z.unknown())),
    }),
    exec: async (nango, input) => {
        const page = input.page ?? 1;
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;
        if (input.modifiedSince) headers['If-Modified-Since'] = input.modifiedSince;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/Contacts',
            params: {
                page,
                ...(input.where ? { where: input.where } : {}),
            },
            headers,
        });

        const contacts = (res.data?.Contacts ?? []) as Array<Record<string, unknown>>;
        return { page, count: contacts.length, contacts };
    },
});
