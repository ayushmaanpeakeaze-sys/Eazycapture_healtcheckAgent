"""HTTP surface for the healthcheck POC, mounted at ``/api/v1/health``.

Day 3 ships two routes:

* ``POST /sync-xero-history/{company_id}/`` — fire-and-forget audit
  dispatch. Returns 202 + ``batch_id`` within a few ms; the actual work
  happens in the Celery worker.
* ``GET  /sync-xero-history-status/{batch_id}/`` — polled by the
  frontend. Pure Redis read; no DB hit.

More routes (trapped-invoices, resolve, suggest-fix, apply-ai-fix,
summary, panorama) land in Days 4-7.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.db import get_db
from app.core.multi_tenant import allowed_company_ids_for, get_current_company_id
from app.core.redis_client import get_redis
from app.modules.healthcheck.schemas import (
    ApplyAiFixRequest,
    AuditConfigUpdate,
    AuditStatusResponse,
    BankBalanceMarkOkRequest,
    BulkActionRequest,
    BulkActionResponse,
    BulkConfirmContactDefaultsRequest,
    CompaniesPanoramaResponse,
    ConfirmContactDefaultsRequest,
    CreditNoteRequest,
    ExcludeAccountRequest,
    FiledNetAssetsRequest,
    RegistrationNumberRequest,
    StatementBalanceRequest,
    DismissRequest,
    DismissResponse,
    DispatchAuditResponse,
    HealthCheckResultItem,
    HealthCheckResultsResponse,
    HealthCheckStatusCounts,
    HealthStatsResponse,
    HealthSummaryResponse,
    MarkOkRequest,
    MarkOkResponse,
    RecheckAttachmentResponse,
    ReenrichDispatchResponse,
    RestoreResponse,
    UploadAttachmentRequest,
    UploadAttachmentResponse,
    ResolveRequest,
    SnoozeRequest,
    SnoozeResponse,
    SuggestFixResponse,
    TrappedInvoiceAI,
    TrappedInvoicesResponse,
)
from app.modules.healthcheck.services.apply_ai_fix_service import ApplyAiFixService
from app.modules.healthcheck.services.audit_service import AuditService
from app.modules.healthcheck.services.panorama_service import CompaniesPanoramaService
from app.modules.healthcheck.services.reenrich_service import ReenrichService
from app.modules.healthcheck.services.attachment_service import AttachmentService
from app.modules.healthcheck.services.resolve_service import ResolveService
from app.modules.healthcheck.services.suggest_fix_service import SuggestFixService
from app.modules.healthcheck.services.trapped_service import TrappedInvoiceService

router = APIRouter(
    prefix="/api/v1/health",
    tags=["healthcheck"],
    # Auth is applied router-wide. When ``JWT_SECRET`` is empty or
    # ``AUTH_DISABLED=true``, the dependency returns a synthetic admin
    # and routes work without an Authorization header. When auth is on,
    # every route here gates on a valid bearer token.
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/sync-xero-history/{company_id}/",
    response_model=DispatchAuditResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Dispatch a historical audit for one company. Returns immediately.",
)
async def sync_xero_history(
    company_id: UUID = Depends(get_current_company_id),
    date_from: Optional[date] = Query(
        None, description="Audit only transactions on/after this date (YYYY-MM-DD)."
    ),
    date_to: Optional[date] = Query(
        None, description="Audit only transactions on/before this date (YYYY-MM-DD)."
    ),
    scope: str = Query(
        "full",
        description="'full' = the whole ledger (all checks). 'duplicates' = the "
        "fast 'Run duplicates only' button — runs ONLY duplicate invoices + bills "
        "(no LLM checks, no contact checks, no AI enrichment).",
    ),
    db: AsyncSession = Depends(get_db),
) -> DispatchAuditResponse:
    """Dispatch a historical audit, optionally scoped to a date period.

    Two buttons map here: ``scope=full`` (the whole-ledger audit) and
    ``scope=duplicates`` (the fast duplicates-only re-run). The frontend's
    Period selector computes start/end; omit both to audit all transactions.
    """
    scope = scope.strip().lower()
    if scope not in ("full", "duplicates"):
        scope = "full"
    service = AuditService(db)
    try:
        return await service.dispatch_audit(
            company_id, date_from=date_from, date_to=date_to, scope=scope,
        )
    finally:
        await service.close()


@router.get(
    "/trapped-invoices/",
    response_model=TrappedInvoicesResponse,
    status_code=status.HTTP_200_OK,
    summary="Paginated feed of post-ledger trapped rows + AI annotations.",
)
async def list_trapped_invoices(
    company_id: UUID = Depends(get_current_company_id),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    issue_type: Optional[str] = Query(None, description="Scope to one check, e.g. 'old_unsettled_sales_credit'. Matches any flagged item of this type."),
    include_dismissed: bool = Query(False, description="True = the 'Show dismissed matches' toggle."),
    include_marked_ok: bool = Query(False, description="True = the 'Show items marked as OK' toggle (supplier checks)."),
    exclude_bank_items: bool = Query(False, description="True = hide Money In/Out documents ('Show Bank payments too' toggle OFF, for the wrong-tax-direction checks)."),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TrappedInvoicesResponse:
    """One DB query + one Redis MGET. Frontend polls this every 2s.

    Resolved / dismissed / accepted / snoozed rows are filtered out at the SQL
    layer using PostgreSQL ``@>`` containment so a missing key never excludes
    everything. ``include_dismissed=true`` reveals dismissed rows (Show-dismissed);
    ``include_marked_ok=true`` reveals marked-OK rows (Show-marked-OK).
    """
    service = TrappedInvoiceService(db, redis)
    return await service.list_trapped(
        company_id=company_id,
        limit=limit,
        offset=offset,
        search_document_id=search,
        include_dismissed=include_dismissed,
        include_marked_ok=include_marked_ok,
        issue_type=issue_type,
        exclude_bank_items=exclude_bank_items,
    )


@router.get(
    "/results/",
    response_model=HealthCheckResultsResponse,
    status_code=status.HTTP_200_OK,
    summary="Audit log — every health-check event (any kind/status) + counts.",
)
async def list_health_check_results(
    company_id: UUID = Depends(get_current_company_id),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="blocked | passed | unavailable | skipped (omit for all)",
    ),
    kind: Optional[str] = Query(
        None, description="preview | pre_ledger | post_ledger (omit for all)",
    ),
    db: AsyncSession = Depends(get_db),
) -> HealthCheckResultsResponse:
    """Full audit-log feed: every recorded health-check verdict for the
    company, filterable by status and kind, with per-status counts.

    Unlike ``/trapped-invoices/`` (blocked post-ledger only), this returns
    every event so the frontend's Audit-log page can show ALL / BLOCKED /
    PASSED / UNAVAILABLE / SKIPPED tabs.
    """
    from sqlalchemy import func, select

    from app.modules.healthcheck.models import Company, HealthCheckResult
    from app.modules.healthcheck.xero_links import xero_deep_link

    # Per-status counts (ignore the status filter; respect the kind filter
    # so the tabs reflect the current Kind selection).
    count_filters = [HealthCheckResult.company_id == company_id]
    if kind:
        count_filters.append(HealthCheckResult.kind == kind.strip().lower())
    count_rows = (
        await db.execute(
            select(HealthCheckResult.status, func.count())
            .where(*count_filters)
            .group_by(HealthCheckResult.status)
        )
    ).all()
    by_status = {s: int(n) for s, n in count_rows}
    counts = HealthCheckStatusCounts(
        all=sum(by_status.values()),
        blocked=by_status.get("blocked", 0),
        passed=by_status.get("passed", 0),
        unavailable=by_status.get("unavailable", 0),
        skipped=by_status.get("skipped", 0),
    )

    # The filtered page of rows.
    row_filters = list(count_filters)
    if status_filter:
        row_filters.append(HealthCheckResult.status == status_filter.strip().lower())

    total = (
        await db.execute(
            select(func.count()).select_from(HealthCheckResult).where(*row_filters)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(HealthCheckResult)
            .where(*row_filters)
            .order_by(HealthCheckResult.ran_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    company = await db.get(Company, company_id)
    shortcode = (company.xero_shortcode or "").strip() or None if company else None

    results = [
        HealthCheckResultItem(
            id=r.id,
            document_id=r.document_id,
            document_type=r.document_type,
            company_id=r.company_id,
            kind=r.kind,
            status=r.status,
            error_msgs=r.error_msgs,
            result=r.result or {},
            ran_at=r.ran_at,
            xero_url=xero_deep_link(r.document_type, r.document_id, shortcode),
        )
        for r in rows
    ]

    return HealthCheckResultsResponse(
        results=results,
        counts=counts,
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/trapped/{row_id}/resolve/",
    status_code=status.HTTP_200_OK,
    summary="Apply field updates + mark trapped row resolved.",
)
async def resolve_trapped(
    row_id: UUID,
    payload: ResolveRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """For Day 5, the Xero PUT is stubbed inside ``ResolveService``.
    Day 6 swaps the stub for the real Nango call without touching this
    route."""
    service = ResolveService(db)
    response = await service.resolve(
        row_id=row_id,
        company_id=company_id,
        field_updates=payload.field_updates,
        resolution_notes=payload.resolution_notes,
    )
    if response.error_code:
        return JSONResponse(
            content=response.model_dump(mode="json"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return response


@router.post(
    "/trapped/{row_id}/void/",
    status_code=status.HTTP_200_OK,
    summary="Void an invoice/bill (Status → VOIDED), with the can't-void-if-paid guard.",
)
async def void_trapped(
    row_id: UUID,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """The 'Void' button on a duplicate. Blocks (400) with a clear message when
    the invoice has a payment/credit note allocated — unallocate in Xero first.
    Writes to real Xero when the org is connected (else a stub response)."""
    service = ResolveService(db)
    response = await service.void(row_id=row_id, company_id=company_id)
    if response.error_code:
        return JSONResponse(
            content=response.model_dump(mode="json"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return response


@router.post(
    "/trapped/{row_id}/credit-note/",
    status_code=status.HTTP_200_OK,
    summary="Create a credit note for an old unpaid invoice (write-off / discount).",
)
async def credit_note_trapped(
    row_id: UUID,
    payload: CreditNoteRequest = Body(default_factory=CreditNoteRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """The 'Credit Note' button. Creates a credit note in Xero crediting the
    invoice and marks the row resolved (real Xero when connected, else a stub)."""
    service = ResolveService(db)
    response = await service.create_credit_note(
        row_id=row_id, company_id=company_id, reason=payload.reason,
    )
    if response.error_code:
        return JSONResponse(
            content=response.model_dump(mode="json"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return response


@router.post(
    "/trapped/{row_id}/dismiss/",
    response_model=DismissResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a trapped row as a false positive.",
)
async def dismiss_trapped(
    row_id: UUID,
    payload: DismissRequest = Body(default_factory=DismissRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> DismissResponse:
    service = ResolveService(db)
    return await service.dismiss(
        row_id=row_id,
        company_id=company_id,
        dismissal_reason=payload.dismissal_reason,
    )


@router.post(
    "/trapped/{row_id}/snooze/",
    response_model=SnoozeResponse,
    status_code=status.HTTP_200_OK,
    summary="Hide a trapped row for N days ('Ignore for 30 days').",
)
async def snooze_trapped(
    row_id: UUID,
    payload: SnoozeRequest = Body(default_factory=SnoozeRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> SnoozeResponse:
    """Snooze a row until it ages back into the feed after ``days``. Use for
    'review later' items that aren't false positives."""
    service = ResolveService(db)
    return await service.snooze(
        row_id=row_id,
        company_id=company_id,
        days=payload.days,
        reason=payload.reason,
    )


@router.post(
    "/trapped/{row_id}/mark-ok/",
    response_model=MarkOkResponse,
    status_code=status.HTTP_200_OK,
    summary="Accept a real flag as a legit/acceptable difference.",
)
async def mark_ok_trapped(
    row_id: UUID,
    payload: MarkOkRequest = Body(default_factory=MarkOkRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> MarkOkResponse:
    """Mark-OK is distinct from dismiss: the flag is *correct*, but the user
    accepts the underlying state (e.g. a known, legit bank-balance difference)."""
    service = ResolveService(db)
    return await service.mark_ok(
        row_id=row_id,
        company_id=company_id,
        reason=payload.reason,
    )


@router.post(
    "/trapped/{row_id}/restore/",
    response_model=RestoreResponse,
    status_code=status.HTTP_200_OK,
    summary="Add a dismissed / marked-OK / snoozed row back to the issue list.",
)
async def restore_trapped(
    row_id: UUID,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> RestoreResponse:
    """Xenon's "Mark as Not OK" / "Add back to issue list" — clears the user
    hide-flags (marked_ok / dismissed / snoozed) so the row returns to the
    actionable feed. Does not touch a genuinely *resolved* row."""
    service = ResolveService(db)
    return await service.restore(row_id=row_id, company_id=company_id)


@router.post(
    "/trapped/{row_id}/recheck-attachment/",
    response_model=RecheckAttachmentResponse,
    status_code=status.HTTP_200_OK,
    summary="'Check Again' — re-check the doc's attachment in Xero; resolve if present.",
)
async def recheck_attachment(
    row_id: UUID,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> RecheckAttachmentResponse:
    """Undocumented Bills "Check Again": re-fetch the bill/payment from Xero. If
    it now has an attachment, drop it from the issue list."""
    return await AttachmentService(db).recheck(row_id=row_id, company_id=company_id)


@router.post(
    "/trapped/{row_id}/attachment/",
    response_model=UploadAttachmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload a file (base64) as an attachment on the Xero document; resolve.",
)
async def upload_attachment(
    row_id: UUID,
    payload: UploadAttachmentRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> UploadAttachmentResponse:
    """Undocumented Bills: upload a PDF/receipt to the Xero bill (base64 body),
    then resolve the issue. The bill is now documented."""
    return await AttachmentService(db).upload(
        row_id=row_id, company_id=company_id,
        filename=payload.filename, content_type=payload.content_type,
        content_base64=payload.content_base64,
    )


@router.post(
    "/trapped/bulk/",
    response_model=BulkActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply one local-state action (dismiss / snooze / mark_ok / restore) to many rows.",
)
async def bulk_action_trapped(
    payload: BulkActionRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> BulkActionResponse:
    """Bulk dismiss / snooze / mark-OK. Each row is applied independently so one
    bad id never aborts the batch; the response reports per-row success."""
    service = ResolveService(db)
    return await service.bulk(
        row_ids=payload.row_ids,
        company_id=company_id,
        action=payload.action,
        days=payload.days,
        reason=payload.reason,
    )


@router.get(
    "/trapped/{row_id}/suggest-fix/",
    response_model=SuggestFixResponse,
    status_code=status.HTTP_200_OK,
    summary="Proxy to the rules engine's /api/v1/suggest-fix for one row.",
)
@router.post(
    "/trapped/{row_id}/suggest-fix/",
    response_model=SuggestFixResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Same as GET — provided because some frontends POST LLM ops.",
)
async def suggest_fix_for_trapped(
    row_id: UUID,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SuggestFixResponse:
    service = SuggestFixService(db, redis=redis)
    return await service.get_suggestion(row_id, company_id)


@router.get(
    "/trapped/{row_id}/ai-insight/",
    response_model=TrappedInvoiceAI,
    status_code=status.HTTP_200_OK,
    summary="Return cached AI insight for a row, or trigger on-demand enrichment.",
)
async def get_ai_insight(
    row_id: UUID,
    company_id: UUID = Depends(get_current_company_id),
    force: bool = Query(False, description="Bypass cache and regenerate from LLM."),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TrappedInvoiceAI:
    """Called by the frontend when the user opens a trapped row.

    Fast path (< 10 ms): Redis hit → return cached insight immediately.
    Slow path (~ 2-3 s): cache miss → enrich this row via the LLM →
    write to Redis → return.  Subsequent opens are always fast.
    Pass ``?force=true`` to bypass cache and regenerate (e.g. after a
    prompt update or when the cached insight looks stale).
    """
    from sqlalchemy import select as _select

    from app.modules.healthcheck.models import HealthCheckResult
    from app.modules.healthcheck.services.trapped_service import _coerce_ai
    from app.modules.ai.schemas import EnrichRowRequest, TrappedRow
    from app.modules.ai import insight_service

    row = (
        await db.execute(
            _select(HealthCheckResult).where(
                HealthCheckResult.id == row_id,
                HealthCheckResult.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Row not found.")

    cache_key = f"health_check_ai:{row.document_id}"

    # Fast path — already enriched (skipped when force=true)
    if not force:
        raw = await redis.get(cache_key)
        cached = _coerce_ai(raw)
        if cached is not None:
            return cached

    # Slow path — enrich on demand, then cache
    result = row.result or {}
    trapped_row = TrappedRow(
        transaction_id=str(row.document_id),
        rule_ids=[str(r) for r in (result.get("rule_ids") or [])],
        messages=str(result.get("messages") or row.error_msgs or ""),
        transaction={
            "type": row.document_type,
            "document_id": str(row.document_id),
            "vendor_name": result.get("vendor_name") or "",
        },
        flagged_items=result.get("flagged") or [],
    )
    record = await insight_service.enrich_row_sync(trapped_row, batch_id=None)
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI enrichment unavailable.",
        )
    return TrappedInvoiceAI(
        explanation=record.explanation,
        severity_ai=record.severity_ai,
        confidence=record.confidence,
        regulatory_ref=record.regulatory_ref,
    )


@router.post(
    "/trapped/{row_id}/apply-ai-fix/",
    status_code=status.HTTP_200_OK,
    summary="Pull a suggestion (or use the override), parse, mark resolved.",
)
async def apply_ai_fix(
    row_id: UUID,
    payload: ApplyAiFixRequest = Body(default_factory=ApplyAiFixRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    suggest_service = SuggestFixService(db)
    resolve_service = ResolveService(db)
    apply_service = ApplyAiFixService(db, suggest_service, resolve_service)
    response = await apply_service.apply(
        row_id=row_id,
        company_id=company_id,
        suggestion_override=payload.suggestion,
    )
    if response.error_code:
        # AI_UNAVAILABLE maps to 503; everything else is a 400.
        http_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if response.error_code == "AI_UNAVAILABLE"
            else status.HTTP_400_BAD_REQUEST
        )
        return JSONResponse(
            content=response.model_dump(mode="json"),
            status_code=http_code,
        )
    return response


@router.get(
    "/sync-xero-history-status/{batch_id}/",
    response_model=AuditStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Poll batch status (Redis-only, frontend-friendly).",
)
async def sync_xero_history_status(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> AuditStatusResponse:
    # No company-scope check here on purpose: the batch_id is opaque +
    # short-TTL, so knowing it is sufficient proof. Adding a company-id
    # query param would force the frontend to thread tenancy through
    # what is otherwise a clean polling URL.
    service = AuditService(db)
    try:
        return await service.get_status(batch_id)
    finally:
        await service.close()


# =====================================================================
# Day 7 — panorama, summary, re-enrich
# =====================================================================

@router.get(
    "/audit-config/",
    status_code=status.HTTP_200_OK,
    summary="Per-client audit configuration — which checks run + date floor.",
)
async def get_audit_config(
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Returns the full grouped rule catalog with each rule's enabled
    state for this company, so the frontend can render the Audit
    Configuration screen."""
    from app.modules.healthcheck.models import Company
    from app.modules.healthcheck.rules_registry import rule_catalog, total_checks
    from app.services.healthcheck.audit_settings import AuditSettings, settings_schema

    company = await db.get(Company, company_id)
    cfg = (company.audit_config or {}) if company else {}
    disabled = set(cfg.get("disabled_rules") or [])
    groups = rule_catalog(disabled)
    return {
        "company_id": str(company_id),
        "total_checks": total_checks(),
        "enabled_checks": total_checks() - len(disabled & {
            r["key"] for g in groups for r in g["rules"]
        }),
        "disabled_rules": sorted(disabled),
        "ignore_before": cfg.get("ignore_before"),
        # Current per-client overrides + the full default set, so the config
        # screen can render each threshold input with its value/placeholder.
        "settings": AuditSettings.clean_overrides(cfg.get("settings")),
        "settings_defaults": AuditSettings().as_json_dict(),
        # Per-check field metadata so the settings screen renders one section
        # per check (each field's label/type/help/default), entirely from the
        # API instead of hardcoding the threshold→check mapping in the UI.
        "settings_schema": settings_schema(),
        "groups": groups,
    }


@router.put(
    "/audit-config/",
    status_code=status.HTTP_200_OK,
    summary="Save per-client audit configuration.",
)
async def put_audit_config(
    payload: AuditConfigUpdate = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Persist which checks are disabled + the optional 'ignore before'
    date. Unknown rule keys are dropped so the config stays clean."""
    from app.modules.healthcheck.models import Company
    from app.modules.healthcheck.rules_registry import (
        ALL_RULE_KEYS, rule_catalog, total_checks,
    )

    from app.services.healthcheck.audit_settings import AuditSettings, settings_schema

    company = await db.get(Company, company_id)
    if company is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Company not found.")

    # Keep only valid keys.
    disabled = sorted(set(payload.disabled_rules) & ALL_RULE_KEYS)
    cfg: dict[str, Any] = {"disabled_rules": disabled}
    if payload.ignore_before:
        cfg["ignore_before"] = payload.ignore_before.strip()
    # Per-client thresholds — sanitise + keep only valid overrides so a bad
    # value can never poison an audit (it's dropped, default applies).
    clean_settings = AuditSettings.clean_overrides(payload.settings)
    if clean_settings:
        cfg["settings"] = clean_settings
    company.audit_config = cfg
    await db.commit()

    groups = rule_catalog(set(disabled))
    return {
        "company_id": str(company_id),
        "total_checks": total_checks(),
        "enabled_checks": total_checks() - len(disabled),
        "disabled_rules": disabled,
        "ignore_before": cfg.get("ignore_before"),
        "settings": clean_settings,
        "settings_defaults": AuditSettings().as_json_dict(),
        "settings_schema": settings_schema(),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Contact Defaults screen — list / confirm (write-back to Xero) / bulk
# ---------------------------------------------------------------------------

@router.get(
    "/coding-options/",
    status_code=status.HTTP_200_OK,
    summary="Account + tax-rate options for the 'Change To' pickers.",
)
async def coding_options(
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """The chart-of-accounts + tax-rate dropdown options used by the
    Unexpected-Account / Unexpected-Tax 'Change To' pickers. Light (no contact
    fetch). ``connected: false`` when the org has no Nango connection."""
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    return await ContactDefaultsService(db).coding_options(company_id)


@router.get(
    "/contact-defaults/",
    status_code=status.HTTP_200_OK,
    summary="List contacts + their 4 default account/tax settings (+ dropdown options).",
)
async def list_contact_defaults(
    company_id: UUID = Depends(get_current_company_id),
    missing_only: bool = Query(True, description="False = the 'Show all Xero contacts' toggle."),
    search: Optional[str] = Query(None, description="Filter by contact name."),
    include_dismissed: bool = Query(False, description="True = the 'show dismissed' view."),
    db: AsyncSession = Depends(get_db),
):
    """Live-fetches contacts + chart-of-accounts + tax-rates. Each row carries
    the contact's current four defaults, which are missing, a `trapped_row_id`
    (for dismiss) and a `dismissed` flag; `accounts` / `tax_rates` are the
    dropdown options. ``connected: false`` when the org has no Nango connection."""
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    service = ContactDefaultsService(db)
    return await service.list_defaults(
        company_id, missing_only=missing_only, search=search,
        include_dismissed=include_dismissed,
    )


@router.post(
    "/contact-defaults/{contact_id}/dismiss/",
    status_code=status.HTTP_200_OK,
    summary="Dismiss a contact from the Contact-Defaults list (persisted).",
)
async def dismiss_contact_defaults(
    contact_id: str,
    payload: DismissRequest = Body(default_factory=DismissRequest),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Persistently hide a contact — the live list honours it on reload
    (until reinstated). Use for contacts you don't want to set defaults on."""
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    service = ContactDefaultsService(db)
    return await service.dismiss(company_id, contact_id, reason=payload.dismissal_reason)


@router.post(
    "/contact-defaults/{contact_id}/reinstate/",
    status_code=status.HTTP_200_OK,
    summary="Un-dismiss a contact (show-dismissed → reinstate).",
)
async def reinstate_contact_defaults(
    contact_id: str,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    service = ContactDefaultsService(db)
    return await service.reinstate(company_id, contact_id)


@router.post(
    "/contact-defaults/{contact_id}/confirm/",
    status_code=status.HTTP_200_OK,
    summary="Write a contact's chosen default account/tax settings to Xero.",
)
async def confirm_contact_defaults(
    contact_id: str,
    payload: ConfirmContactDefaultsRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """The 'Confirm' button — writes the four defaults (any subset) back to the
    Xero contact. Only the fields provided are written."""
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    service = ContactDefaultsService(db)
    return await service.confirm(company_id, contact_id, payload.model_dump())


@router.post(
    "/contact-defaults/bulk-confirm/",
    status_code=status.HTTP_200_OK,
    summary="Write default account/tax settings for many contacts at once.",
)
async def bulk_confirm_contact_defaults(
    payload: BulkConfirmContactDefaultsRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Bulk 'Confirm' — each item is {contact_id, defaults}. Per-contact result
    so one failure doesn't abort the batch."""
    from app.modules.healthcheck.services.contact_defaults_service import (
        ContactDefaultsService,
    )
    service = ContactDefaultsService(db)
    items = [
        {"contact_id": it.contact_id, "defaults": it.defaults.model_dump()}
        for it in payload.items
    ]
    return await service.bulk_confirm(company_id, items)


@router.get(
    "/stats/",
    response_model=HealthStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Aggregated issue counts for charts and graphs.",
)
async def health_stats(
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> HealthStatsResponse:
    """Returns counts broken down by issue type and severity — feeds
    donut charts, bar charts, and health score gauge in the frontend."""
    from collections import Counter
    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from app.modules.healthcheck.models import HealthCheckResult, AuditBatch
    from app.modules.healthcheck.schemas import IssueTypeCount, SeverityCount

    rows = (
        await db.execute(
            select(HealthCheckResult).where(
                HealthCheckResult.company_id == company_id,
                HealthCheckResult.kind == "post_ledger",
            )
        )
    ).scalars().all()

    total = len(rows)

    now_ts = int(datetime.now(timezone.utc).timestamp())

    def _is_open(r) -> bool:
        res = r.result or {}
        snoozed_until = res.get("snoozed_until_ts")
        currently_snoozed = (
            isinstance(snoozed_until, (int, float)) and snoozed_until > now_ts
        )
        return (
            not res.get("resolved")
            and not res.get("dismissed")
            and not res.get("marked_ok")
            and not res.get("auto_cleared")   # stale (re-run no longer flags it)
            and not currently_snoozed
            and r.status == "blocked"
        )

    open_rows = [r for r in rows if _is_open(r)]
    open_issues = len(open_rows)
    resolved = sum(1 for r in rows if (r.result or {}).get("resolved"))
    dismissed = sum(1 for r in rows if (r.result or {}).get("dismissed"))

    # Split: documents (invoices/bills) vs contacts — contacts are hygiene,
    # not documents, so they shouldn't inflate the "documents trapped" count
    # or drag the document-based health score.
    open_contact_issues = sum(
        1 for r in open_rows if (r.document_type or "").upper() == "CONTACT"
    )
    open_document_issues = open_issues - open_contact_issues

    type_counter: Counter = Counter()
    sev_counter: Counter = Counter()
    type_severity: dict = {}
    for r in open_rows:
        for f in (r.result or {}).get("flagged") or []:
            itype = f.get("issue_type", "unknown")
            sev = f.get("severity", "medium")
            type_counter[itype] += 1
            sev_counter[sev] += 1
            type_severity[itype] = sev

    # Denominators = BROADEST recent completed audit (MAX), so a period-scoped
    # run (April = 22 docs) doesn't shrink them below a prior full sweep.
    audited_documents = (
        await db.execute(
            select(func.max(AuditBatch.total)).where(
                AuditBatch.company_id == company_id,
                AuditBatch.status == "completed",
            )
        )
    ).scalar_one_or_none() or 0
    contacts_audited = (
        await db.execute(
            select(func.max(AuditBatch.contacts_total)).where(
                AuditBatch.company_id == company_id,
                AuditBatch.status == "completed",
            )
        )
    ).scalar_one_or_none() or 0

    # BLENDED health score: documents AND contacts both count. Every fixable
    # issue (whichever pool) drags it down.
    total_audited = audited_documents + contacts_audited
    health_score = None
    if total_audited > 0:
        health_score = max(0, min(
            100, int(100 * (1 - open_issues / total_audited))
        ))

    by_type = [
        IssueTypeCount(issue_type=k, count=v, severity=type_severity.get(k, "medium"))
        for k, v in type_counter.most_common()
    ]
    by_sev = [
        SeverityCount(severity=k, count=v)
        for k, v in sev_counter.most_common()
    ]

    return HealthStatsResponse(
        company_id=company_id,
        health_score=health_score,
        total_issues=total,
        open_issues=open_issues,
        open_document_issues=open_document_issues,
        open_contact_issues=open_contact_issues,
        audited_documents=int(audited_documents),
        audited_contacts=int(contacts_audited),
        resolved_issues=resolved,
        dismissed_issues=dismissed,
        by_issue_type=by_type,
        by_severity=by_sev,
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/companies-panorama/",
    response_model=CompaniesPanoramaResponse,
    status_code=status.HTTP_200_OK,
    summary="Health-score dashboard across every active company.",
)
async def companies_panorama(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> CompaniesPanoramaResponse:
    """Cross-company dashboard.

    Admins (and team members in "all" mode) see every company. Team
    members in "selected" mode see only the companies assigned to them.
    """
    allowed = await allowed_company_ids_for(db, user)
    service = CompaniesPanoramaService(db)
    return await service.get_panorama(days=days, allowed_company_ids=allowed)


@router.get(
    "/summary/",
    response_model=HealthSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Per-company health summary + top issues.",
)
async def company_summary(
    company_id: UUID = Depends(get_current_company_id),
    days: int = Query(30, ge=1, le=365),
    top_n_issues: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> HealthSummaryResponse:
    service = CompaniesPanoramaService(db)
    return await service.get_company_summary(
        company_id=company_id,
        days=days,
        top_n_issues=top_n_issues,
    )


@router.post(
    "/re-enrich/",
    response_model=ReenrichDispatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Backfill AI annotations on trapped rows missing them in Redis.",
)
async def reenrich_missing(
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ReenrichDispatchResponse:
    """Finds trapped rows whose ``health_check_ai:{doc}`` key is empty
    and queues them for one-at-a-time re-enrichment so Groq's TPM cap
    doesn't clip the burst again."""
    service = ReenrichService(db, redis)
    rows = await service.list_missing_rows(company_id)
    # Imported inline so importing this router module doesn't drag in
    # Celery at boot.
    from app.modules.healthcheck.tasks import reenrich_missing_task
    task = reenrich_missing_task.delay(rows)
    return ReenrichDispatchResponse(
        company_id=company_id,
        task_id=str(task.id),
        eligible_rows=len(rows),
    )


# =====================================================================
# Opening Balance Differences (filed accounts vs Xero) + Bank Balance Check
# =====================================================================

@router.get(
    "/opening-balance-differences/",
    status_code=status.HTTP_200_OK,
    summary="Filed (Companies House) vs Xero Net Assets per period end.",
)
async def opening_balance_differences(
    include_dismissed: bool = Query(False),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    return await OpeningBalanceService(db).list_differences(
        company_id, include_dismissed=include_dismissed)


@router.get(
    "/opening-balance-differences/{period_end}/late-transactions/",
    status_code=status.HTTP_200_OK,
    summary="Transactions dated in the closed period, posted most recently.",
)
async def opening_balance_late_transactions(
    period_end: str,
    limit: int = Query(5, ge=1, le=100),
    offset: int = Query(0, ge=0),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    return await OpeningBalanceService(db).late_transactions(
        company_id, period_end, limit=limit, offset=offset)


@router.post(
    "/opening-balance-differences/registration-number/",
    status_code=status.HTTP_200_OK,
    summary="Set the Companies House registration number (enables auto-fetch).",
)
async def set_registration_number(
    payload: RegistrationNumberRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    await OpeningBalanceService(db).set_registration_number(
        company_id, payload.registration_number)
    return {"company_id": str(company_id), "registration_number": payload.registration_number}


@router.post(
    "/opening-balance-differences/filed-net-assets/",
    status_code=status.HTTP_200_OK,
    summary="Manually record filed Net Assets for a period end.",
)
async def set_filed_net_assets(
    payload: FiledNetAssetsRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    await OpeningBalanceService(db).set_filed_net_assets(
        company_id, payload.period_end.isoformat(), payload.net_assets)
    return {"ok": True, "period_end": payload.period_end.isoformat()}


@router.post(
    "/opening-balance-differences/{period_end}/dismiss/",
    status_code=status.HTTP_200_OK,
    summary="Hide an opening-balance difference period.",
)
async def dismiss_opening_balance(
    period_end: str,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    await OpeningBalanceService(db).dismiss(company_id, period_end)
    return {"period_end": period_end, "dismissed": True}


@router.post(
    "/opening-balance-differences/{period_end}/restore/",
    status_code=status.HTTP_200_OK,
    summary="Un-dismiss an opening-balance difference period.",
)
async def restore_opening_balance(
    period_end: str,
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService
    await OpeningBalanceService(db).restore(company_id, period_end)
    return {"period_end": period_end, "dismissed": False}


@router.get(
    "/bank-balance-check/",
    status_code=status.HTTP_200_OK,
    summary="Per bank account: Per Bank Statement (manual) vs Per Xero TB.",
)
async def bank_balance_check(
    period_end: str = Query(..., description="Closing date YYYY-MM-DD"),
    show_all: bool = Query(False),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.bank_balance_service import BankBalanceService
    return await BankBalanceService(db).list_differences(
        company_id, period_end, show_all=show_all)


@router.post(
    "/bank-balance-check/statement-balance/",
    status_code=status.HTTP_200_OK,
    summary="Record the physical 'Per Bank Statement' balance for an account.",
)
async def set_bank_statement_balance(
    payload: StatementBalanceRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.bank_balance_service import BankBalanceService
    await BankBalanceService(db).set_statement_balance(
        company_id, payload.account_code, payload.period_end.isoformat(), payload.balance)
    return {"ok": True}


@router.post(
    "/bank-balance-check/{account_code}/exclude/",
    status_code=status.HTTP_200_OK,
    summary="Exclude / reinstate a bank account from the check.",
)
async def exclude_bank_account(
    account_code: str,
    payload: ExcludeAccountRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.bank_balance_service import BankBalanceService
    await BankBalanceService(db).exclude_account(
        company_id, account_code, excluded=payload.excluded)
    return {"account_code": account_code, "excluded": payload.excluded}


@router.post(
    "/bank-balance-check/mark-ok/",
    status_code=status.HTTP_200_OK,
    summary="Accept a bank balance difference as OK for this period end.",
)
async def bank_balance_mark_ok(
    payload: BankBalanceMarkOkRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.bank_balance_service import BankBalanceService
    await BankBalanceService(db).mark_ok(
        company_id, payload.account_code, payload.period_end.isoformat(), ok=payload.ok)
    return {"account_code": payload.account_code, "marked_ok": payload.ok}


@router.get(
    "/unreconciled-bank-items/",
    status_code=status.HTTP_200_OK,
    summary="Per bank account: unreconciled Received/Spent transaction counts.",
)
async def unreconciled_bank_items(
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Ledger-side unreconciled transactions per bank account. ``unexplained``
    (feed statement lines) is always null — it needs Xero's gated Finance API;
    ``unexplained_available: false`` flags that for the UI."""
    from app.modules.healthcheck.services.unreconciled_bank_service import (
        UnreconciledBankService,
    )
    return await UnreconciledBankService(db).list_accounts(company_id)


@router.post(
    "/unreconciled-bank-items/{account_code}/exclude/",
    status_code=status.HTTP_200_OK,
    summary="Exclude / reinstate a bank account from the unreconciled check.",
)
async def exclude_unreconciled_account(
    account_code: str,
    payload: ExcludeAccountRequest = Body(...),
    company_id: UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.healthcheck.services.unreconciled_bank_service import (
        UnreconciledBankService,
    )
    await UnreconciledBankService(db).exclude_account(
        company_id, account_code, excluded=payload.excluded)
    return {"account_code": account_code, "excluded": payload.excluded}
