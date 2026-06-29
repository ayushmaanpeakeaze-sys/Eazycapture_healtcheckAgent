"""Data-access layer for :class:`HealthCheckResult` rows.

Every public method takes ``company_id`` as a required positional
argument and includes it in every WHERE clause — *even single-row
fetches by primary key* — so a missing check at the router layer can't
silently expose a different tenant's row. Repository tests in
``tests/test_multi_tenant.py`` lock this in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import BigInteger, Numeric, String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import HealthCheckResult

# Resolution / dismissal state is stored inside ``result`` JSONB so we
# don't need a migration every time the workflow grows new states.
_FLAG_RESOLVED = "resolved"
_FLAG_DISMISSED = "dismissed"
# "marked_ok" = real flag, but the user accepts it (legit difference) — distinct
# from "dismissed" (false positive). "snoozed_until_ts" = epoch seconds the row
# stays hidden until; it reappears in the feed once that time passes.
_FLAG_MARKED_OK = "marked_ok"
_FLAG_SNOOZED_UNTIL_TS = "snoozed_until_ts"
# "auto_cleared" = the latest audit run no longer flags this document, so it
# drops out of the actionable feed (but stays in the DB for history). Set by the
# audit task's reconcile step, never by a user action.
_FLAG_AUTO_CLEARED = "auto_cleared"

# The flags a USER sets to hide a still-valid row. ``restore`` clears exactly
# these (the "Mark as Not OK" / "Add back to issue list" action) — it never touches
# ``resolved`` (genuinely fixed) or ``auto_cleared`` (latest audit dropped it).
_USER_HIDE_FLAGS = (
    _FLAG_MARKED_OK, "mark_ok_reason", "marked_ok_by_user_id",
    _FLAG_DISMISSED, "dismissal_reason", "dismissed_by_user_id",
    _FLAG_SNOOZED_UNTIL_TS, "snoozed_until", "snooze_reason", "snoozed_by_user_id",
)


def _open_row_filters(
    now_ts: int,
    *,
    include_dismissed: bool = False,
    include_marked_ok: bool = False,
) -> list:
    """Predicates that keep only *currently actionable* rows: not resolved,
    not dismissed, not accepted (marked OK), and not currently snoozed. A
    snoozed row reappears automatically once ``snoozed_until_ts`` passes.

    ``include_dismissed=True`` is the "Show dismissed matches" toggle — it drops
    the dismissed exclusion. ``include_marked_ok=True`` is the "Show items marked
    as OK" toggle (the supplier checks) — it drops the marked-OK exclusion.
    Resolved / auto-cleared / snoozed always stay hidden."""
    snooze_ts = cast(
        HealthCheckResult.result[_FLAG_SNOOZED_UNTIL_TS].astext, BigInteger,
    )
    filters = [
        ~HealthCheckResult.result.contains({_FLAG_RESOLVED: True}),
        # Stale rows the latest run no longer flags — always hidden from the
        # actionable feed (history stays in the DB, not surfaced here).
        ~HealthCheckResult.result.contains({_FLAG_AUTO_CLEARED: True}),
        or_(snooze_ts.is_(None), snooze_ts <= now_ts),
    ]
    if not include_marked_ok:
        filters.append(~HealthCheckResult.result.contains({_FLAG_MARKED_OK: True}))
    if not include_dismissed:
        filters.append(~HealthCheckResult.result.contains({_FLAG_DISMISSED: True}))
    return filters


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class HealthCheckResultRepository:
    """Async repository for the ``health_check_result`` table.

    Construct with an open :class:`AsyncSession`; the caller owns the
    session's commit/rollback lifecycle.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ----------------------- reads ---------------------------------

    async def list_post_ledger_trapped(
        self,
        company_id: UUID,
        *,
        limit: int = 50,
        include_resolved: bool = False,
        include_dismissed: bool = False,
    ) -> list[HealthCheckResult]:
        """Latest ``post_ledger`` rows in ``blocked`` state for the tenant.

        ``include_resolved`` / ``include_dismissed`` widen the result set
        — by default both are excluded so the frontend only sees
        actionable items.
        """
        stmt = (
            select(HealthCheckResult)
            .where(
                HealthCheckResult.company_id == company_id,
                HealthCheckResult.kind == "post_ledger",
                HealthCheckResult.status == "blocked",
            )
            .order_by(HealthCheckResult.ran_at.desc())
            .limit(limit)
        )
        if not include_resolved:
            stmt = stmt.where(
                func.coalesce(
                    HealthCheckResult.result[_FLAG_RESOLVED].astext.cast(JSONB),
                    func.to_jsonb(False),
                )
                == func.to_jsonb(False)
            )
        if not include_dismissed:
            stmt = stmt.where(
                func.coalesce(
                    HealthCheckResult.result[_FLAG_DISMISSED].astext.cast(JSONB),
                    func.to_jsonb(False),
                )
                == func.to_jsonb(False)
            )
        # marked-OK + currently-snoozed are always hidden from the actionable
        # feed (no widening flag — they're user-chosen "hide" states).
        now_ts = _now_ts()
        snooze_ts = cast(
            HealthCheckResult.result[_FLAG_SNOOZED_UNTIL_TS].astext, BigInteger,
        )
        stmt = stmt.where(~HealthCheckResult.result.contains({_FLAG_MARKED_OK: True}))
        stmt = stmt.where(~HealthCheckResult.result.contains({_FLAG_AUTO_CLEARED: True}))
        stmt = stmt.where(or_(snooze_ts.is_(None), snooze_ts <= now_ts))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_post_ledger_trapped_paginated(
        self,
        company_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        search_document_id: Optional[str] = None,
        include_dismissed: bool = False,
        include_marked_ok: bool = False,
        issue_type: Optional[str] = None,
        exclude_bank_items: bool = False,
    ) -> tuple[list[HealthCheckResult], int, Decimal]:
        """Page of latest blocked post-ledger rows + total row count + total
        value (the "Total Potential Errors" £ sum across ALL matching rows).

        Excludes anything marked ``resolved`` / ``dismissed`` inside the
        ``result`` JSONB. Filter uses Postgres ``@>`` (``.contains``) so
        the predicate works for any JSONB shape — never the path-equality
        trap that returns NULL on missing keys.

        ``issue_type`` scopes the feed to ONE check (e.g. ``old_unsettled_sales_credit``)
        so each check renders its own page with correct pagination + total —
        matched against any element of the row's ``result.flagged`` array.

        ``search_document_id`` is a partial-UUID match against the
        document_id column (frontend search box).
        """
        base_filters = [
            HealthCheckResult.company_id == company_id,
            HealthCheckResult.kind == "post_ledger",
            HealthCheckResult.status == "blocked",
            *_open_row_filters(
                _now_ts(),
                include_dismissed=include_dismissed,
                include_marked_ok=include_marked_ok,
            ),
        ]
        if issue_type:
            # Row matches if ANY flagged item is this issue type (JSONB @>).
            base_filters.append(
                HealthCheckResult.result.contains(
                    {"flagged": [{"issue_type": issue_type}]}
                )
            )
        if exclude_bank_items:
            # "Show Bank payments too" toggle OFF → hide Money In/Out documents.
            base_filters.append(
                func.upper(func.coalesce(HealthCheckResult.document_type, "")).notin_(
                    ("SPEND", "RECEIVE")
                )
            )
        if search_document_id:
            base_filters.append(
                cast(HealthCheckResult.document_id, String).ilike(
                    f"%{search_document_id}%",
                )
            )

        rows_stmt = (
            select(HealthCheckResult)
            .where(*base_filters)
            .order_by(HealthCheckResult.ran_at.desc())
            .limit(limit)
            .offset(offset)
        )
        count_stmt = (
            select(func.count())
            .select_from(HealthCheckResult)
            .where(*base_filters)
        )
        # "Total Potential Errors" — outstanding (amount_due, = RemainingCredit for
        # credit notes) when present, else the transaction value (amount).
        _due = func.nullif(HealthCheckResult.result["amount_due"].astext, "").cast(Numeric)
        _amt = func.nullif(HealthCheckResult.result["amount"].astext, "").cast(Numeric)
        value_stmt = (
            select(func.coalesce(func.sum(func.coalesce(_due, _amt, 0)), 0))
            .select_from(HealthCheckResult)
            .where(*base_filters)
        )

        rows = (await self.db.execute(rows_stmt)).scalars().all()
        total = (await self.db.execute(count_stmt)).scalar_one()
        total_value = (await self.db.execute(value_stmt)).scalar_one()
        return list(rows), int(total), Decimal(str(total_value or 0))

    async def find_by_id(
        self,
        row_id: UUID,
        company_id: UUID,
    ) -> Optional[HealthCheckResult]:
        """Single row by id, scoped by company. Returns ``None`` cross-tenant."""
        stmt = select(HealthCheckResult).where(
            and_(
                HealthCheckResult.id == row_id,
                HealthCheckResult.company_id == company_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_post_ledger_blocked(
        self,
        document_id: UUID,
        company_id: UUID,
    ) -> bool:
        """True if any ``post_ledger`` row in ``blocked`` state exists for
        this document within the tenant. Used by the audit dispatcher to
        avoid double-flagging an already-trapped document.
        """
        stmt = (
            select(HealthCheckResult.id)
            .where(
                HealthCheckResult.document_id == document_id,
                HealthCheckResult.company_id == company_id,
                HealthCheckResult.kind == "post_ledger",
                HealthCheckResult.status == "blocked",
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ----------------------- writes --------------------------------

    async def record_decision(
        self,
        *,
        decision: dict[str, Any],
        document_id: UUID,
        document_type: str,
        company_id: UUID,
        user_id: Optional[UUID],
        kind: str,
        status: str,
        target_ledger: Optional[str] = None,
    ) -> HealthCheckResult:
        """Insert a new verdict row.

        ``decision`` is stored verbatim as the ``result`` JSONB and may
        include rule flags, AI enrichment fields, etc. ``target_ledger``
        is appended into ``result`` under a stable key so consumers can
        branch on it without a schema change.
        """
        result_payload: dict[str, Any] = dict(decision) if decision else {}
        if user_id is not None:
            result_payload.setdefault("recorded_by_user_id", str(user_id))
        if target_ledger is not None:
            result_payload.setdefault("target_ledger", target_ledger)

        row = HealthCheckResult(
            company_id=company_id,
            document_id=document_id,
            document_type=document_type,
            kind=kind,
            status=status,
            error_msgs=str(result_payload.get("error_msgs") or "") or None,
            result=result_payload,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def mark_resolved(
        self,
        row_id: UUID,
        company_id: UUID,
        *,
        resolution_notes: Optional[str] = None,
        resolved_by_user_id: Optional[UUID] = None,
        xero_response: Optional[dict[str, Any]] = None,
    ) -> Optional[HealthCheckResult]:
        """Mark a trapped row resolved. Locks the row FOR UPDATE so
        concurrent writers can't clobber the JSONB ``result`` field.
        Returns ``None`` cross-tenant or when the row doesn't exist.
        """
        row = await self._select_for_update(row_id, company_id)
        if row is None:
            return None
        result = dict(row.result or {})
        result[_FLAG_RESOLVED] = True
        if resolution_notes:
            result["resolution_notes"] = resolution_notes
        if resolved_by_user_id:
            result["resolved_by_user_id"] = str(resolved_by_user_id)
        if xero_response is not None:
            result["xero_response"] = xero_response
        row.result = result
        await self.db.flush()
        return row

    async def mark_dismissed(
        self,
        row_id: UUID,
        company_id: UUID,
        *,
        dismissal_reason: Optional[str] = None,
        dismissed_by_user_id: Optional[UUID] = None,
    ) -> Optional[HealthCheckResult]:
        """Mark a trapped row dismissed (false positive). Locks FOR UPDATE."""
        row = await self._select_for_update(row_id, company_id)
        if row is None:
            return None
        result = dict(row.result or {})
        result[_FLAG_DISMISSED] = True
        if dismissal_reason:
            result["dismissal_reason"] = dismissal_reason
        if dismissed_by_user_id:
            result["dismissed_by_user_id"] = str(dismissed_by_user_id)
        row.result = result
        await self.db.flush()
        return row

    async def mark_snoozed(
        self,
        row_id: UUID,
        company_id: UUID,
        *,
        snoozed_until_ts: int,
        snoozed_until_iso: str,
        snooze_reason: Optional[str] = None,
        snoozed_by_user_id: Optional[UUID] = None,
    ) -> Optional[HealthCheckResult]:
        """Hide a trapped row until ``snoozed_until_ts`` (epoch seconds), after
        which it reappears in the feed. The "Ignore for N days" button. Locks
        FOR UPDATE so the JSONB ``result`` isn't clobbered."""
        row = await self._select_for_update(row_id, company_id)
        if row is None:
            return None
        result = dict(row.result or {})
        result[_FLAG_SNOOZED_UNTIL_TS] = int(snoozed_until_ts)
        result["snoozed_until"] = snoozed_until_iso
        if snooze_reason:
            result["snooze_reason"] = snooze_reason
        if snoozed_by_user_id:
            result["snoozed_by_user_id"] = str(snoozed_by_user_id)
        row.result = result
        await self.db.flush()
        return row

    async def mark_ok(
        self,
        row_id: UUID,
        company_id: UUID,
        *,
        reason: Optional[str] = None,
        marked_ok_by_user_id: Optional[UUID] = None,
    ) -> Optional[HealthCheckResult]:
        """Accept a real flag as a legit/acceptable difference (distinct from
        ``dismiss`` = false positive). Hides it from the feed. Locks FOR UPDATE."""
        row = await self._select_for_update(row_id, company_id)
        if row is None:
            return None
        result = dict(row.result or {})
        result[_FLAG_MARKED_OK] = True
        if reason:
            result["mark_ok_reason"] = reason
        if marked_ok_by_user_id:
            result["marked_ok_by_user_id"] = str(marked_ok_by_user_id)
        row.result = result
        await self.db.flush()
        return row

    async def restore(
        self,
        row_id: UUID,
        company_id: UUID,
    ) -> Optional[HealthCheckResult]:
        """Clear the user 'hide' flags (marked_ok / dismissed / snoozed) so the
        row returns to the actionable feed — the "Mark as Not OK" / "Add back
        to issue list" action. Does NOT touch ``resolved`` (genuinely fixed) or
        ``auto_cleared`` (latest audit no longer flags it). Locks FOR UPDATE."""
        row = await self._select_for_update(row_id, company_id)
        if row is None:
            return None
        result = dict(row.result or {})
        for key in _USER_HIDE_FLAGS:
            result.pop(key, None)
        row.result = result
        await self.db.flush()
        return row

    # ----------------------- internal ------------------------------

    async def _select_for_update(
        self,
        row_id: UUID,
        company_id: UUID,
    ) -> Optional[HealthCheckResult]:
        """SELECT ... FOR UPDATE on (id, company_id) — caller is in a txn."""
        stmt = (
            select(HealthCheckResult)
            .where(
                HealthCheckResult.id == row_id,
                HealthCheckResult.company_id == company_id,
            )
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
