import { createAction } from 'nango';
import * as z from 'zod';

/**
 * update-invoice — write a field/line-item fix back to a Xero invoice or bill.
 *
 * The single generic write the backend already calls (IntegrationService.update_invoice
 * → action_update_invoice → this). Drives several check "buttons":
 *   • Approve (Unapproved Invoices/Bills)        → { Status: "AUTHORISED" }
 *   • Delete  (created in error)                 → { Status: "DELETED" }
 *   • Recode line (low-cost / capital / misalloc)→ { lineItems: [{ LineItemID, AccountCode }] }
 *
 * Input is `{ invoiceId, lineItems?, ...fields }`. Everything except invoiceId/
 * lineItems is applied verbatim to the Xero invoice object, so the caller sends
 * Xero-cased field names (Status, etc.). Xero updates are a POST of the document.
 *
 * ⚠️ Xero line-item gotcha: a POST with `LineItems` REPLACES all lines — the
 * caller must send the FULL line set (changed + unchanged), not just the edited
 * line, or the others are dropped.
 */
export default createAction({
    description: 'Update a Xero invoice/bill — Status (approve/delete) and/or line items (recode).',
    version: '1.0.0',
    input: z
        .object({
        tenantId: z.string().optional(),
            invoiceId: z.string().min(1),
            lineItems: z.array(z.record(z.string(), z.unknown())).optional(),
        })
        .passthrough(), // allow arbitrary Xero invoice fields (Status, DueDate, …)
    output: z.object({
        invoiceId: z.string(),
        status: z.string().optional(),
        updated: z.boolean(),
    }),
    exec: async (nango, input) => {
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;

        const { invoiceId, lineItems, tenantId: _t, ...fields } = input as Record<string, unknown> & {
            invoiceId: string;
            lineItems?: unknown[];
        };

        const invoice: Record<string, unknown> = { InvoiceID: invoiceId, ...fields };
        if (lineItems) invoice['LineItems'] = lineItems;

        const res = await nango.post({
            endpoint: `api.xro/2.0/Invoices/${invoiceId}`,
            headers,
            data: { Invoices: [invoice] },
        });

        const inv = ((res.data?.Invoices ?? [])[0] ?? {}) as { Status?: string };
        return { invoiceId, status: inv.Status, updated: true };
    },
});
