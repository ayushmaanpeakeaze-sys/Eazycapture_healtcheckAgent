"""Bank Balance Check — runtime orchestration.

Per bank account at a selected period end:
  * **Per Xero TB**       — the account's GL balance (Xero TrialBalance at date)
  * **Per Bank Statement** — the user-entered physical statement balance
  * **Per Xero Statement** — the bank-feed balance → ``None`` (Xero's Finance API
    is gated; the standard Accounting API only exposes the GL, which equals the
    TrialBalance, so an auto statement column would be meaningless here)
  * **Difference**         — Per Bank Statement − Per Xero TB

Accounts are flagged when a manual statement balance is present and the
difference exceeds the tolerance (and the account isn't excluded / marked-OK).
``show_all`` includes every bank account (the "Show all bank accounts" toggle).
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from app.modules.healthcheck.services.company_config import CompanyConfigStore
from app.modules.healthcheck.xero_links import xero_deep_link
from app.modules.integrations.service import IntegrationService
from app.services.healthcheck.audit_settings import AuditSettings
from app.services.insights.bank import _parse_trial_balance_balances

logger = logging.getLogger("hcpoc.bank_balance_service")


def _dec(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _ok_key(code: str, period_end: str) -> str:
    return f"{code}|{period_end}"


class BankBalanceService:
    def __init__(self, db, integration: Optional[IntegrationService] = None) -> None:
        self._db = db
        self._store = CompanyConfigStore(db)
        self._integration = integration or IntegrationService()

    async def list_differences(
        self, company_id: UUID, period_end: str, *, show_all: bool = False,
    ) -> dict[str, Any]:
        company, cfg = await self._store.load(company_id)
        if company is None:
            return {"period_end": period_end, "total_value": 0.0, "items": []}

        conn = getattr(company, "nango_connection_id", None)
        tenant = getattr(company, "xero_tenant_id", None)
        shortcode = getattr(company, "xero_shortcode", None)
        bb = self._store.bank_balance(cfg)
        settings = AuditSettings.from_config(cfg.get("settings"))
        tol = abs(settings.bank_balance_tolerance)

        excluded = {str(c).strip().upper() for c in (bb.get("excluded") or [])}
        marked_ok = set(bb.get("marked_ok") or [])
        manual = bb.get("statement") or {}   # {code: {period_end: "value"}}

        # Bank accounts from the chart of accounts.
        coa = await self._integration.fetch_chart_of_accounts(conn, tenant) or []
        bank_accounts = {
            str(a.get("AccountID")): {
                "code": (a.get("Code") or "").strip(),
                "name": (a.get("Name") or "").strip(),
            }
            for a in coa if isinstance(a, dict) and a.get("Type") == "BANK"
        }
        # GL balance per account at the period end.
        tb_report = await self._integration.fetch_trial_balance(conn, tenant, period_end)
        gl = _parse_trial_balance_balances(tb_report)   # {account_id: {code, balance}}

        # Note / supporting-doc counts per account for this period end (one
        # grouped query each — no N+1), so the UI can badge "2 notes · 1 doc".
        note_counts, doc_counts = await self._annotation_counts(company_id, period_end)

        items, total = [], Decimal("0")
        for acc_id, info in bank_accounts.items():
            code = info["code"]
            if code.upper() in excluded:
                continue
            tb_balance = (gl.get(acc_id) or {}).get("balance")
            stmt = _dec((manual.get(code) or {}).get(period_end))
            difference = (stmt - tb_balance) if (stmt is not None and tb_balance is not None) else None
            is_ok = _ok_key(code, period_end) in marked_ok
            flagged = (
                difference is not None and abs(difference) > tol and not is_ok
            )
            if not flagged and not show_all:
                continue
            if flagged:
                total += abs(difference)
            items.append({
                "id": code,
                "account_code": code or None,
                "account_name": info["name"] or None,
                "period_end": period_end,
                "per_bank_statement": float(stmt) if stmt is not None else None,
                "per_xero_statement": None,   # Finance API gated — see module docstring
                "per_xero_tb": float(tb_balance) if tb_balance is not None else None,
                "difference": float(difference) if difference is not None else None,
                "marked_ok": is_ok,
                "notes_count": note_counts.get(code, 0),
                "documents_count": doc_counts.get(code, 0),
                "process_url": xero_deep_link("BANK", acc_id, shortcode),
            })
        items.sort(key=lambda r: abs(r["difference"] or 0), reverse=True)
        return {"period_end": period_end, "total_value": float(total), "items": items}

    async def _annotation_counts(
        self, company_id: UUID, period_end: str,
    ) -> tuple[dict[str, int], dict[str, int]]:
        from sqlalchemy import func, select

        from app.modules.healthcheck.models import BankDocument, BankNote

        notes = dict(
            (
                await self._db.execute(
                    select(BankNote.account_code, func.count())
                    .where(
                        BankNote.company_id == company_id,
                        BankNote.period_end == period_end,
                    )
                    .group_by(BankNote.account_code)
                )
            ).all()
        )
        docs = dict(
            (
                await self._db.execute(
                    select(BankDocument.account_code, func.count())
                    .where(
                        BankDocument.company_id == company_id,
                        BankDocument.period_end == period_end,
                    )
                    .group_by(BankDocument.account_code)
                )
            ).all()
        )
        return {str(k): int(v) for k, v in notes.items()}, {str(k): int(v) for k, v in docs.items()}

    # --- write ------------------------------------------------------------
    async def set_statement_balance(
        self, company_id: UUID, account_code: str, period_end: str, balance: Decimal,
    ) -> None:
        company, cfg = await self._store.load(company_id)
        if company is None:
            return
        bb = dict(self._store.bank_balance(cfg))
        statement = dict(bb.get("statement") or {})
        per_acc = dict(statement.get(account_code) or {})
        per_acc[period_end] = str(balance)
        statement[account_code] = per_acc
        bb["statement"] = statement
        cfg["bank_balance"] = bb
        await self._store.save(company, cfg)

    async def exclude_account(self, company_id: UUID, account_code: str, *, excluded: bool) -> None:
        company, cfg = await self._store.load(company_id)
        if company is None:
            return
        bb = dict(self._store.bank_balance(cfg))
        current = set(bb.get("excluded") or [])
        current.add(account_code) if excluded else current.discard(account_code)
        bb["excluded"] = sorted(current)
        cfg["bank_balance"] = bb
        await self._store.save(company, cfg)

    async def mark_ok(self, company_id: UUID, account_code: str, period_end: str, *, ok: bool) -> None:
        company, cfg = await self._store.load(company_id)
        if company is None:
            return
        bb = dict(self._store.bank_balance(cfg))
        current = set(bb.get("marked_ok") or [])
        key = _ok_key(account_code, period_end)
        current.add(key) if ok else current.discard(key)
        bb["marked_ok"] = sorted(current)
        cfg["bank_balance"] = bb
        await self._store.save(company, cfg)
