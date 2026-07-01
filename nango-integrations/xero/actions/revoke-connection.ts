import { createAction } from 'nango';
import * as z from 'zod';

export default createAction({
    description:
        "Revoke one Xero organisation's access (Xero DELETE /connections/{id}) so it returns to the allow-access screen without touching other orgs on the same login.",
    version: '1.0.0',
    input: z.object({
        tenantId: z.string().min(1),
    }),
    output: z.object({
        revoked: z.boolean(),
        connectionId: z.string().nullable(),
    }),
    exec: async (nango, input) => {
        const res = await nango.get({ endpoint: 'connections' });
        const conns = (res.data ?? []) as Array<{ id?: string; tenantId?: string }>;
        const match = conns.find((c) => c.tenantId === input.tenantId);
        if (!match?.id) return { revoked: false, connectionId: null };

        await nango.delete({ endpoint: `connections/${match.id}` });
        return { revoked: true, connectionId: match.id };
    },
});
