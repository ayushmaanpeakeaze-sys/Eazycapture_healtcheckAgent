import { createAction } from 'nango';
import * as z from 'zod';

/**
 * create-credit-note — raise a Xero credit note (e.g. to write off / settle an
 * old unpaid invoice or bill).
 *
 * Drives the "Create Credit Note" button on the Old Unpaid checks. The caller
 * passes the document direction (ACCRECCREDIT for a customer credit, ACCPAYCREDIT
 * for a supplier credit), the contact, and the line items. Optionally pass
 * `status` (default AUTHORISED) and `reference`.
 *
 * Allocation to the original invoice is a SECOND call in Xero
 * (POST /CreditNotes/{id}/Allocations) — kept out of this action so it stays one
 * clean create; allocate via a follow-up if needed.
 */
export default createAction({
    description: 'Create a Xero credit note (customer ACCRECCREDIT or supplier ACCPAYCREDIT).',
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().optional(),
        type: z.enum(['ACCRECCREDIT', 'ACCPAYCREDIT']),
        contactId: z.string().min(1),
        lineItems: z.array(z.record(z.string(), z.unknown())).min(1),
        date: z.string().optional(),       // YYYY-MM-DD
        reference: z.string().optional(),
        status: z.string().optional(),     // default AUTHORISED
    }),
    output: z.object({
        creditNoteId: z.string().optional(),
        number: z.string().optional(),
        status: z.string().optional(),
        created: z.boolean(),
    }),
    exec: async (nango, input) => {
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;

        const creditNote: Record<string, unknown> = {
            Type: input.type,
            Contact: { ContactID: input.contactId },
            LineItems: input.lineItems,
            Status: input.status ?? 'AUTHORISED',
        };
        if (input.date) creditNote['Date'] = input.date;
        if (input.reference) creditNote['Reference'] = input.reference;

        const res = await nango.post({
            endpoint: 'api.xro/2.0/CreditNotes',
            headers,
            data: { CreditNotes: [creditNote] },
        });

        const cn = ((res.data?.CreditNotes ?? [])[0] ?? {}) as {
            CreditNoteID?: string;
            CreditNoteNumber?: string;
            Status?: string;
        };
        return {
            creditNoteId: cn.CreditNoteID,
            number: cn.CreditNoteNumber,
            status: cn.Status,
            created: Boolean(cn.CreditNoteID),
        };
    },
});
