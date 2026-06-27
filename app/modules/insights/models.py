"""Insights snapshot table.

One row per company holding the pre-computed KPIs. A nightly Celery task
refreshes it (heavy Xero fetches happen there, off the request path); the API
serves the snapshot instantly and the firm-summary rolls up across snapshots.
Summary columns are duplicated out of ``payload`` so the firm rollup can filter
fast without unpacking JSON.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, uuid_pk


class ClientInsightSnapshot(Base):
    __tablename__ = "client_insight_snapshot"
    __table_args__ = (
        # One current snapshot per company (upsert target).
        Index("uq_cis_company", "company_id", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.id", ondelete="CASCADE"),
        nullable=False,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    # "ok" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- summary columns (for the firm rollup) ---
    net_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    tax_estimate: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    cash: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    cash_coverage: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    working_capital: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    working_capital_healthy: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    distributable_reserves: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    net_asset_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    dla_detected: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    dla_overdrawn: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # --- full per-KPI payload (for instant per-org serve) ---
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


__all__ = ["ClientInsightSnapshot"]
