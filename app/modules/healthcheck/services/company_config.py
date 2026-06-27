"""Read/write helper for per-company operational state stored on
``Company.audit_config`` (a JSON column) — so the Opening Balance and Bank
Balance checks can persist manual entries, exclusions and dismissals WITHOUT a
new migration.

Layout under ``audit_config`` (alongside the existing ``disabled_rules`` /
``settings`` / ``ignore_before`` keys, which this helper never touches)::

    registration_number: "12345678"
    opening_balance: { filed: {"2023-09-30": "324.00"}, dismissed: ["2022-09-30"] }
    bank_balance:    { statement: {"090": {"2026-03-31": "64749.69"}},
                       excluded: ["091"], marked_ok: ["090|2026-03-31"] }
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import Company


class CompanyConfigStore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def load(self, company_id: UUID) -> tuple[Optional[Company], dict[str, Any]]:
        company = await self._db.get(Company, company_id)
        cfg = dict(company.audit_config or {}) if company else {}
        return company, cfg

    async def save(self, company: Company, cfg: dict[str, Any]) -> None:
        # Reassign the whole dict so SQLAlchemy flags the JSON column dirty.
        company.audit_config = cfg
        await self._db.commit()

    # --- typed sub-tree accessors ------------------------------------------
    @staticmethod
    def registration_number(cfg: dict[str, Any]) -> Optional[str]:
        v = (cfg.get("registration_number") or "").strip()
        return v or None

    @staticmethod
    def opening_balance(cfg: dict[str, Any]) -> dict[str, Any]:
        ob = cfg.get("opening_balance")
        return ob if isinstance(ob, dict) else {}

    @staticmethod
    def bank_balance(cfg: dict[str, Any]) -> dict[str, Any]:
        bb = cfg.get("bank_balance")
        return bb if isinstance(bb, dict) else {}
