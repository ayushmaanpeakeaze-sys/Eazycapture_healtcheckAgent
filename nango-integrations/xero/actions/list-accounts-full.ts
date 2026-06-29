import { createAction } from 'nango';
import * as z from 'zod';

/**
 * list-accounts-full — the full Xero chart of accounts (code, name, Type,
 * TaxType, status, Class).
 *
 * The COA is the join table for almost every check (account TYPE drives capital
 * / low-cost / tax-missing / misallocated; TaxType + Class help the wrong-tax
 * checks). Accounts don't paginate in Xero (the set is small) so this is a
 * single call returning the raw `Accounts` array.
 */
export default createAction({
    description: 'Full Xero chart of accounts (code, name, type, tax type, status).',
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        where: z.string().optional(),
        modifiedSince: z.string().optional(),
    }),
    output: z.object({
        count: z.number(),
        accounts: z.array(z.record(z.string(), z.unknown())),
    }),
    exec: async (nango, input) => {
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;
        if (input.modifiedSince) headers['If-Modified-Since'] = input.modifiedSince;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/Accounts',
            params: { ...(input.where ? { where: input.where } : {}) },
            headers,
        });

        const accounts = (res.data?.Accounts ?? []) as Array<Record<string, unknown>>;
        return { count: accounts.length, accounts };
    },
});
