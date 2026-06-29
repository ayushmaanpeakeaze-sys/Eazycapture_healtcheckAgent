import { createAction } from 'nango';
import * as z from 'zod';

/**
 * update-contact — write a fix back to a Xero contact.
 *
 * Drives several check buttons:
 *   • Contact Defaults missing → set SalesDefaultAccountCode / PurchasesDefaultAccountCode
 *     / AccountsReceivableTaxType / AccountsPayableTaxType
 *   • Inactive contact          → { ContactStatus: "ARCHIVED" }
 *
 * Input is `{ contactId, ...fields }`. Everything except contactId is applied
 * verbatim to the Xero contact object, so the caller sends Xero-cased field
 * names. Xero updates are a POST of the contact with the changed fields.
 */
export default createAction({
    description: 'Update a Xero contact — defaults (account/tax) and/or status (archive).',
    version: '1.0.0',
    input: z
        .object({
        tenantId: z.string().optional(),
            contactId: z.string().min(1),
        })
        .passthrough(), // allow arbitrary Xero contact fields
    output: z.object({
        contactId: z.string(),
        status: z.string().optional(),
        updated: z.boolean(),
    }),
    exec: async (nango, input) => {
        const meta = (await nango.getMetadata()) as { tenant_id?: string } | null;
        const tenantId = input.tenantId ?? meta?.tenant_id;
        const headers: Record<string, string> = {};
        if (tenantId) headers['xero-tenant-id'] = tenantId;

        const { contactId, tenantId: _t, ...fields } = input as Record<string, unknown> & { contactId: string };
        const contact: Record<string, unknown> = { ContactID: contactId, ...fields };

        const res = await nango.post({
            endpoint: `api.xro/2.0/Contacts/${contactId}`,
            headers,
            data: { Contacts: [contact] },
        });

        const c = ((res.data?.Contacts ?? [])[0] ?? {}) as { ContactStatus?: string };
        return { contactId, status: c.ContactStatus, updated: true };
    },
});
