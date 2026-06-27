"""Health-check orchestrator — ties deterministic + LLM passes together.

``run_batch_health_check`` applies the per-client audit config (disabled
rules + ignore-before date), runs every enabled check, and returns the
flagged issues.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Awaitable, Callable, Optional

from app.schemas.transaction import (
    BatchHealthCheckRequest,
    BatchHealthCheckResponse,
    FlaggedIssue,
)
from app.services.healthcheck.audit_settings import AuditSettings
from app.services.healthcheck.shared import (
    _allowed_tax_codes,
    _BANK_TXN_TYPES,
    _coa_lookup,
    _coa_summary,
    _coa_type_lookup,
    _format_tax_codes_hint,
    _noop_issues,
    _tax_direction_map,
)
from app.checks.documents import _find_undocumented_bills
from app.checks.fixed_assets import (
    _find_capital_items,
    _find_low_cost_fixed_assets,
)
from app.checks.tax import (
    _find_multi_tax_code_suppliers,
    _find_purchase_tax_missing,
    _find_purchase_tax_on_invoices,
    _find_sales_tax_missing,
    _find_sales_tax_on_bills,
    _find_unexpected_tax_codes,
)
from app.services.healthcheck.deterministic import (
    _find_bill_direct_payments,
    _find_invoice_direct_deposits,
    _find_direction_mismatches,
    _find_duplicate_bills,
    _find_misallocated_items,
    _find_multi_account_suppliers,
    _find_opening_balance_differences,
    _find_unexpected_accounts,
    _inspect_transaction,
    amount_outlier_flag,
    find_amount_outlier_candidates,
)
from app.modules.ai.checks_llm import (
    _batched_category_audit,
    _llm_anomaly_review,
)

ProgressCallback = Callable[[dict], Awaitable[None]]

async def run_batch_health_check(
    req: BatchHealthCheckRequest,
    progress_callback: Optional[ProgressCallback] = None,
) -> BatchHealthCheckResponse:
    """Concurrently audit a batch of transactions and return all flagged issues."""
    context = req.context
    allowed_tax_codes = _allowed_tax_codes(context)
    tax_dir = _tax_direction_map(context)
    # No contact aliasing: every check keys on the real Xero ContactID. Duplicate
    # contacts are only FLAGGED for the user to Merge/Dismiss in Xero — we never
    # silently merge two ContactIDs into one ledger for any check. (The check
    # functions still accept a ``contact_alias`` arg for call-compatibility; we
    # pass None so they fall back to the raw ContactID.)
    contact_alias = None
    tax_codes_hint = _format_tax_codes_hint(context)
    coa_summary = _coa_summary(context)
    coa_lookup = _coa_lookup(context)
    coa_type_lookup = _coa_type_lookup(context)
    # Per-contact default accounts → enables default-based Unexpected-Account
    # (empty map → the check falls back to its frequency heuristic).
    contact_defaults_map = {
        cd.contact_id.strip(): {
            "sales": cd.sales_account, "purchase": cd.purchase_account,
            "sales_tax": cd.sales_tax, "purchase_tax": cd.purchase_tax,
        }
        for cd in (context.contact_defaults if context else [])
        if cd.contact_id and cd.contact_id.strip()
    }

    today = date.today()

    # --- Per-client audit config ---------------------------------------
    disabled = set(req.disabled_rules or [])
    # Per-client tunable thresholds (duplicate window, overdue days, outlier
    # multiple, supplier dominance, capital pre-filter, …). Defaults match the
    # historical constants, so behaviour is unchanged unless the client
    # overrides a value on the Audit Configuration screen.
    settings = AuditSettings.from_config(req.settings)

    # "Ignore transactions before" — drop anything dated earlier so no
    # check ever sees them.
    transactions = req.transactions
    if req.ignore_before is not None:
        transactions = [t for t in transactions if t.date >= req.ignore_before]
        if not transactions:
            return BatchHealthCheckResponse(flagged=[])

    # Split out bank transactions (Money In / Money Out). They feed ONLY the
    # Unexpected-Account / Unexpected-Tax checks (vs the contact's default
    # account). Every OTHER check keeps seeing just invoices/bills/credits —
    # exactly today's behaviour — so duplicates/ageing/tax-missing never pair or
    # flag a bank transaction.
    bank_transactions = [
        t for t in transactions if (t.type or "").strip().upper() in _BANK_TXN_TYPES
    ]
    transactions = [
        t for t in transactions if (t.type or "").strip().upper() not in _BANK_TXN_TYPES
    ]

    # Per-transaction deterministic rules (no LLM): tax/vendor/required-field checks.
    per_tx_results = [
        _inspect_transaction(tx, allowed_tax_codes, tax_codes_hint, today, settings)
        for tx in transactions
    ]

    # Both LLM passes run in parallel — category audit and capital review share
    # the same concurrency window so total latency = max(both), not sum.
    # Skip a pass entirely when all the rules it produces are disabled
    # (saves LLM tokens + latency).
    run_category = "wrong_category" not in disabled
    run_anomaly = "anomaly" not in disabled
    run_amount_outlier = "amount_outlier" not in disabled
    # Global kill-switch: when LLM checks are disabled (or Groq is unreachable in
    # this environment), skip every LLM pass so the audit stays fast + fully
    # deterministic. Amount-outlier still runs as a raw deterministic flag.
    from app.core.config import settings as _app_settings
    if not _app_settings.LLM_CHECKS_ENABLED:
        run_category = run_anomaly = False

    # Amount-outlier candidates (deterministic, cheap). Feed the LLM anomaly
    # review when enabled; otherwise emit them as raw amount_outlier flags.
    outlier_candidates = find_amount_outlier_candidates(transactions, contact_alias, settings)
    do_anomaly_llm = run_anomaly and bool(outlier_candidates)

    # All three LLM passes runhaa in parallel — total latency = max, not sum.
    category_task = (
        _batched_category_audit(
            transactions, coa_summary, coa_lookup, coa_type_lookup,
            progress_callback=progress_callback,
            audit_settings=settings,
        )
        if (run_category and coa_summary is not None)
        else _noop_issues()
    )
    # Capital review is now FULLY deterministic (low_cost_fixed_asset +
    # capital_item_review, both below) — no LLM capital pass.
    capital_task = _noop_issues()
    anomaly_task = (
        _llm_anomaly_review(outlier_candidates, settings)
        if do_anomaly_llm
        else _noop_issues()
    )
    category_issues, capital_issues, anomaly_issues = await asyncio.gather(
        category_task, capital_task, anomaly_task,
    )

    flagged: list[FlaggedIssue] = []
    for issues in per_tx_results:
        flagged.extend(issues)
    flagged.extend(category_issues)
    flagged.extend(capital_issues)  # always empty — capital is deterministic now
    flagged.extend(anomaly_issues)
    # Capital checks (both deterministic — account + amount, no LLM). Per Xenon
    # these run over invoices, bills AND bank items (Money In / Money Out), so
    # they get ``transactions + bank_transactions``:
    #   • low_cost_fixed_asset  — FIXED-asset line BELOW the capitalisation
    #     threshold → too cheap to capitalise, should be expensed.
    #   • capital_item_review   — monitored EXPENSE line ABOVE the threshold →
    #     too big to expense, may be a capital item. Mirror image of the above.
    capital_universe = transactions + bank_transactions
    flagged.extend(_find_low_cost_fixed_assets(capital_universe, coa_type_lookup, coa_lookup, settings))
    flagged.extend(_find_capital_items(capital_universe, coa_lookup, coa_type_lookup, settings))
    # If the LLM anomaly pass didn't run, fall back to the deterministic
    # amount_outlier flags so outliers are still surfaced.
    if not do_anomaly_llm and run_amount_outlier:
        flagged.extend(amount_outlier_flag(c) for c in outlier_candidates)
    # Duplicate invoices/bills key on the REAL ContactID — never the contact
    # alias — so two distinct ContactIDs are always treated as separate parties.
    flagged.extend(_find_duplicate_bills(transactions, None, settings))
    flagged.extend(_find_opening_balance_differences(transactions, coa_lookup))
    flagged.extend(_find_direction_mismatches(transactions, coa_type_lookup))
    # Multi-Account Suppliers (Xenon): checks the contact's bill line items AND
    # Money-Out bank payments → gets invoices/bills PLUS bank transactions.
    flagged.extend(_find_multi_account_suppliers(transactions + bank_transactions, coa_lookup, contact_alias, settings))
    flagged.extend(_find_multi_tax_code_suppliers(transactions + bank_transactions, contact_alias, settings))
    # Unexpected-Account / Unexpected-Tax also check Money In/Out (bank txns) vs
    # the contact's default — so they get invoices/bills PLUS bank transactions.
    flagged.extend(_find_unexpected_accounts(transactions + bank_transactions, coa_lookup, contact_defaults_map))
    flagged.extend(_find_unexpected_tax_codes(transactions + bank_transactions, contact_defaults_map))
    # Bill-or-Direct-Payment: unpaid bill matched to a direct SPEND payment.
    flagged.extend(_find_bill_direct_payments(transactions, bank_transactions, settings))
    # Invoice-or-Direct-Deposit: unpaid invoice matched to a direct RECEIVE deposit.
    flagged.extend(_find_invoice_direct_deposits(transactions, bank_transactions, settings))
    # Misallocated Items (Xenon): vague-account line over the threshold — checks
    # bills/invoices AND Money In / Money Out → gets bank transactions too.
    flagged.extend(_find_misallocated_items(transactions + bank_transactions, coa_lookup, settings))
    # Undocumented Bills: supplier bill (or Money Out) with no Xero attachment.
    # Money Out is flagged too; the frontend hides it by default (exclude_bank_items).
    flagged.extend(_find_undocumented_bills(transactions + bank_transactions, settings))
    # Tax-missing checks only make sense for a VAT-registered org. Skip when the
    # org is explicitly flagged non-VAT; run as before when unknown (None).
    org_vat = context.org_is_vat_registered if context else None
    if org_vat is not False:
        # Tax-missing (Xenon): account-TYPE driven, so they get invoices/bills PLUS
        # Money In/Out — the account-type filter routes income lines to sales and
        # expense lines to purchase.
        tax_universe = transactions + bank_transactions
        flagged.extend(_find_purchase_tax_missing(tax_universe, coa_lookup, coa_type_lookup, settings))
        flagged.extend(_find_sales_tax_missing(tax_universe, coa_lookup, coa_type_lookup, settings))
    # Wrong-direction VAT (sales code on a bill / purchase code on an invoice).
    # Include bank items (Money Out / Money In) so the "Show Bank payments too"
    # toggle can reveal them; the frontend hides them by default.
    flagged.extend(_find_sales_tax_on_bills(transactions + bank_transactions, tax_dir))
    flagged.extend(_find_purchase_tax_on_invoices(transactions + bank_transactions, tax_dir))

    # Drop any flag whose rule the client disabled on the Audit
    # Configuration screen. (LLM passes were already skipped above; this
    # also covers the deterministic rules in one place.)
    if disabled:
        flagged = [f for f in flagged if f.issue_type not in disabled]

    return BatchHealthCheckResponse(flagged=flagged)
