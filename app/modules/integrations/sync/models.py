"""ORM models for the DB-backed Xero sync.

Two tables, both company-scoped (multi-tenant: every row carries
``company_id``, NOT NULL, indexed — every query MUST filter on it):

  ``xero_sync_state``  — one row per (company, entity). Holds the incremental
                         **watermark** (the high-water ``UpdatedDateUTC`` we've
                         synced) plus bookkeeping (last run, status, counts).

  ``xero_document``    — the mirrored data. One row per (company, entity,
                         xero_id) holding the RAW Xero JSON. We store raw (not
                         typed columns) so the audit's existing reshape logic
                         maps it exactly as it did the live payload — "only the
                         data source changes, checks stay unchanged".
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, uuid_pk

# The entities we mirror. The first five sync INCREMENTALLY (custom actions
# honour If-Modified-Since); the last three are small / watermark-less and
# full-refresh each sync. Kept here as the single source of truth so the
# engine, db_read and tests agree.
SYNC_ENTITIES: tuple[str, ...] = (
    "invoice",
    "bank_transaction",
    "credit_note",
    "contact",
    "account",
    "tax_rate",
    "payment",
    "organisation",
)


class XeroSyncState(Base):
    """Per-(company, entity) sync watermark + run metadata."""

    __tablename__ = "xero_sync_state"
    __table_args__ = (
        UniqueConstraint("company_id", "entity", name="uq_sync_state_company_entity"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # invoice | bank_transaction | credit_note | contact | account | tax_rate |
    # payment | organisation
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    # High-water mark: the max UpdatedDateUTC we've durably stored for this
    # entity. NULL → never synced (next run is a FULL sync). The next
    # incremental sync asks Xero for ``UpdatedDateUTC >= watermark − overlap``.
    watermark_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_full_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ok | error | in_progress
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Rows touched on the last run (for observability / the Refresh UI).
    last_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class XeroDocument(Base):
    """One mirrored Xero record (raw JSON), keyed by (company, entity, xero_id)."""

    __tablename__ = "xero_document"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "entity", "xero_id",
            name="uq_xero_document_company_entity_xeroid",
        ),
        # The audit's read path: all rows for one company+entity.
        Index("ix_xero_document_company_entity", "company_id", "entity"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    # Xero's native id for this record: InvoiceID / BankTransactionID /
    # CreditNoteID / ContactID / AccountID / PaymentID / OrganisationID, or —
    # for entities Xero gives no id (TaxRate) — a natural key (TaxType).
    xero_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The complete Xero object exactly as the action/proxy returned it, so the
    # audit's reshape sees byte-for-byte what the live fetch produced.
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Parsed UpdatedDateUTC (from Xero's /Date(ms+0000)/) — drives the watermark
    # and lets us prune/order. NULL for watermark-less entities (tax rates).
    updated_date_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["XeroSyncState", "XeroDocument", "SYNC_ENTITIES"]
