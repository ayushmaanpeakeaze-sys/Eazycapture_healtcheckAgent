"""One-off: archive stray TEST contacts in live Xero (e.g. 'abc', 'bcd').

These pollute the duplicate-contacts check (a bare 'abc' matches the demo
supplier 'ABC LIMITED' 100% after the legal-suffix strip). Archiving hides
them from the ledger without hard-deleting. Usage:

    python -m scripts.archive_junk_contacts abc bcd
"""
import asyncio
import sys

from app.modules.integrations.service import IntegrationService


async def _main(names: list[str]) -> None:
    targets = {n.strip().lower() for n in names if n.strip()}
    if not targets:
        print("no target names given"); return

    svc = IntegrationService()
    live = await svc.find_live_xero_connection()
    if not live:
        print("no live Xero connection found"); return
    connection_id, tenant_id = live
    print(f"connection={connection_id[:8]}… tenant={tenant_id[:8]}…")

    contacts = await svc._nango.fetch_xero_contacts(connection_id, tenant_id)
    print(f"fetched {len(contacts)} contacts")

    hits = [c for c in contacts if (c.get("Name") or "").strip().lower() in targets]
    if not hits:
        print(f"no contacts matched {sorted(targets)}"); return

    for c in hits:
        cid, name = c.get("ContactID"), c.get("Name")
        status = (c.get("ContactStatus") or "").upper()
        if status == "ARCHIVED":
            print(f"  already archived: {name} ({cid[:8]}…)"); continue
        res = await svc._nango.update_xero_contact(
            connection_id, tenant_id, cid, {"ContactStatus": "ARCHIVED"},
        )
        ok = bool(res and not res.get("error"))
        print(f"  {'ARCHIVED' if ok else 'FAILED  '}: {name} ({cid[:8]}…)")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:] or ["abc", "bcd"]))
