"""DB-backed Xero sync — incremental synchronisation.

The audit historically fetched EVERY Xero entity live on every run (5k
invoices, 15k bank txns …) even when nothing changed. This package mirrors
Xero into company-scoped DB tables and keeps them fresh with an *incremental*
sync (Xero's ``If-Modified-Since`` watermark), so the audit reads from the DB
and only changed records are pulled from Xero.

Layout:
  - ``models``   — ``XeroSyncState`` (per-entity watermark) + ``XeroDocument``
                   (raw Xero JSON store).
  - ``engine``   — ``SyncEngine``: generic full + incremental sync per entity.
  - ``db_read``  — read helpers the audit uses instead of live fetches; they
                   return the SAME raw Xero dict shape so reshape/checks are
                   100% unchanged.

Reads → DB (incremental sync). Writes → Actions/proxy (unchanged). Full Nango
Sync deferred until much larger scale.
"""
from __future__ import annotations

from app.modules.integrations.sync.models import XeroDocument, XeroSyncState

__all__ = ["XeroDocument", "XeroSyncState"]
