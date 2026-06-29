"""SQLAlchemy 2.0 ORM models for the healthcheck (audit) domain.

This module owns only the audit-domain tables — Company (the tenant) and
the documents/results hung off it. Identity (User, UserCompanyAccess) lives
in ``app.modules.auth.models``; delivery tracking (NotificationLog) lives in
``app.modules.notifications.models``. Each module owns its own tables.

Multi-tenancy: every tenant-scoped table carries ``company_id`` (NOT NULL,
indexed). Every repository query must filter on it — that contract is
enforced at the query layer, not here.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, uuid_pk


class Company(Base):
    """Tenant. Every other tenant-scoped row points back here."""

    __tablename__ = "company"
    __table_args__ = (
        # An org's natural key is (connection, tenant) — NOT tenant alone,
        # because two accountants can each connect the same client org under
        # different connection_ids. Partial so seed/demo rows (NULL keys)
        # don't collide and the webhook upsert stays idempotent.
        Index(
            "uq_company_connection_tenant",
            "nango_connection_id",
            "xero_tenant_id",
            unique=True,
            postgresql_where=text(
                "nango_connection_id IS NOT NULL AND xero_tenant_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # The tenant (firm/workspace) that owns this org. Companies are isolated per
    # firm — a firm only ever sees its own. Nullable for legacy rows (backfilled
    # to a default firm by the migration); set on every new connect.
    firm_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("firm.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    xero_tenant_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nango_connection_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Org-scoped shortcode (e.g. ``!S9bXm``) used in modern Xero deep-links.
    # Populated by the webhook on ``auth.creation`` so the "Open in Xero"
    # button forces the right tenant context.
    xero_shortcode: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-client audit configuration set on the frontend's Audit
    # Configuration screen. Shape:
    #   {"disabled_rules": ["currency_mismatch", ...],
    #    "ignore_before": "2025-01-01"}
    # NULL / empty → every check runs, no date floor.
    audit_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    health_check_results: Mapped[list["HealthCheckResult"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    audit_batches: Mapped[list["AuditBatch"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Invoice(Base):
    """An invoice or bill mirrored from Xero (or seeded for demo)."""

    __tablename__ = "invoice"
    __table_args__ = (
        Index("ix_invoice_company_status", "company_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_number: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vendor_name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    amount_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # DRAFT | SUBMITTED | AUTHORISED | PAID | VOIDED | DELETED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # ACCREC | ACCPAY | ACCRECCREDIT | ACCPAYCREDIT
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    account_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False, default="GBP")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    company: Mapped["Company"] = relationship(back_populates="invoices")
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class InvoiceLineItem(Base):
    """One line on an invoice. Mirrors Xero's LineItems[]."""

    __tablename__ = "invoice_line_item"

    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("1"),
    )
    unit_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    account_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tax_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    line_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")


class HealthCheckResult(Base):
    """One audit verdict for one document. ``result`` JSONB carries the
    flagged items + any AI enrichment fields (severity_ai, explanation,
    regulatory_ref, …).
    """

    __tablename__ = "health_check_result"
    __table_args__ = (
        # Single-column indexes declared explicitly with the names created in
        # migration 0001 (short ``ix_hcr_*`` form) — rather than via
        # ``index=True`` (which would use SQLAlchemy's default long names and
        # make autogenerate perpetually want to rename them).
        Index("ix_hcr_company_id", "company_id"),
        Index("ix_hcr_document_id", "document_id"),
        Index("ix_hcr_ran_at", "ran_at"),
        Index("ix_hcr_company_ran_at", "company_id", "ran_at"),
        Index("ix_hcr_document_ran_at", "document_id", "ran_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # pre_ledger | post_ledger | preview
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # passed | blocked | unavailable | skipped
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_msgs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    company: Mapped["Company"] = relationship(back_populates="health_check_results")


class AuditBatch(Base):
    """One historical-audit run. Counters drive the polling status endpoint."""

    __tablename__ = "audit_batch"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # in_progress | completed | failed
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_trapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # How many contacts this run audited — the contact denominator for the
    # blended health score (documents + contacts).
    contacts_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audit_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    ai_enriched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_enrichment_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    company: Mapped["Company"] = relationship(back_populates="audit_batches")


class BankNote(Base):
    """A note attached to one bank account at one period end (Bank Balance
    Check). Internal to EazyCapture — never sent to Xero. Team members can be
    @-tagged via ``tagged_user_ids``."""

    __tablename__ = "bank_note"
    __table_args__ = (
        Index("ix_bank_note_lookup", "company_id", "account_code", "period_end"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)
    period_end: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # User-ids @-tagged in the note (so the UI can notify / render mentions).
    tagged_user_ids: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class BankDocument(Base):
    """A supporting file (bank statement, reconciliation spreadsheet …) uploaded
    against one bank account at one period end (Bank Balance Check). Internal to
    EazyCapture — bytes live in our DB, never sent to Xero."""

    __tablename__ = "bank_document"
    __table_args__ = (
        Index("ix_bank_document_lookup", "company_id", "account_code", "period_end"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)
    period_end: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The file bytes live in the DB (statements are small). Swap for
    # object storage (S3/GCS) when volumes grow.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


__all__ = [
    "Company",
    "Invoice",
    "InvoiceLineItem",
    "HealthCheckResult",
    "AuditBatch",
    "BankNote",
    "BankDocument",
]
