import { createAction } from 'nango';
import * as z from 'zod';

/**
 * get-trial-balance — the Xero Trial Balance report (every account's balance
 * as at a date).
 *
 * Powers the Bank Balance Check ("Balance in Xero") and the Directors' Loan
 * Account insight. Reports are a single call (no pagination); this returns the
 * one report object from the `Reports` array. The `tenantId` input is threaded
 * into the `xero-tenant-id` header so the call targets the correct org on a
 * multi-org connection.
 */
export default createAction({
    description: "Xero Trial Balance report — every account's balance as at a date.",
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        date: z.string().optional(), // "as at" date, YYYY-MM-DD
    }),
    output: z.object({
        report: z.record(z.string(), z.unknown()).nullable(),
    }),
    exec: async (nango, input) => {
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;

        const res = await nango.get({
            endpoint: 'api.xro/2.0/Reports/TrialBalance',
            params: { ...(input.date ? { date: input.date } : {}) },
            headers,
        });

        const reports = (res.data?.Reports ?? []) as Array<Record<string, unknown>>;
        return { report: reports[0] ?? null };
    },
});
