"""Opening Balance + Bank Balance services — orchestration with mocked Xero.

DB + IntegrationService are mocked so these run without Postgres/Nango. They
verify the wiring: manual entries (on audit_config) + Xero reports → rows,
plus the write paths (dismiss, exclude, mark-ok, manual entry persistence).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.healthcheck.services.bank_balance_service import BankBalanceService
from app.modules.healthcheck.services.opening_balance_service import OpeningBalanceService


def _fake_db(company):
    db = MagicMock()
    db.get = AsyncMock(return_value=company)
    db.commit = AsyncMock()
    # bank-balance counts the per-account notes/docs via db.execute(...).all();
    # return an empty grouped result so those queries are a no-op in unit tests.
    _empty = MagicMock(); _empty.all.return_value = []
    db.execute = AsyncMock(return_value=_empty)
    return db


def _company(audit_config):
    return SimpleNamespace(
        audit_config=audit_config,
        nango_connection_id="conn", xero_tenant_id="tenant", xero_shortcode="SHORT",
    )


def _bs(net_assets):
    return {"Rows": [
        {"RowType": "SummaryRow", "Cells": [{"Value": "Net Assets"}, {"Value": net_assets}]},
    ]}


def _tb(acc_id, balance):
    """Synthetic TrialBalance: balance = last-two cells (debit - credit)."""
    return {"Rows": [{"RowType": "Section", "Rows": [
        {"RowType": "Row", "Cells": [
            {"Value": "Business Bank Account (090)",
             "Attributes": [{"Id": "account", "Value": acc_id}]},
            {"Value": ""}, {"Value": ""},
            {"Value": str(balance)}, {"Value": "0"},
        ]},
    ]}]}


# ---------------- Opening Balance ----------------

def test_opening_balance_manual_filed_vs_xero():
    company = _company({"opening_balance": {"filed": {"2023-09-30": "324"}}})
    integ = MagicMock()
    integ.fetch_balance_sheet = AsyncMock(return_value=_bs("21368"))
    svc = OpeningBalanceService(_fake_db(company), integration=integ)

    out = asyncio.run(svc.list_differences(uuid4()))
    assert out["total_value"] == 21044.0
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert item["period_end"] == "2023-09-30"
    assert item["net_assets_filed"] == 324.0
    assert item["net_assets_xero"] == 21368.0
    assert item["difference"] == -21044.0
    assert item["filed_source"] == "manual"


def test_opening_balance_dismiss_hides_row():
    company = _company({"opening_balance": {
        "filed": {"2023-09-30": "324"}, "dismissed": ["2023-09-30"]}})
    integ = MagicMock()
    integ.fetch_balance_sheet = AsyncMock(return_value=_bs("21368"))
    svc = OpeningBalanceService(_fake_db(company), integration=integ)

    # hidden by default
    assert asyncio.run(svc.list_differences(uuid4()))["items"] == []
    # visible with include_dismissed
    shown = asyncio.run(svc.list_differences(uuid4(), include_dismissed=True))
    assert len(shown["items"]) == 1 and shown["items"][0]["dismissed"] is True
    # dismissed rows don't count toward the total
    assert shown["total_value"] == 0.0


def test_opening_balance_below_threshold_skipped():
    company = _company({"opening_balance": {"filed": {"2023-09-30": "21368.50"}}})
    integ = MagicMock()
    integ.fetch_balance_sheet = AsyncMock(return_value=_bs("21368"))   # 50p diff < £1
    svc = OpeningBalanceService(_fake_db(company), integration=integ)
    assert asyncio.run(svc.list_differences(uuid4()))["items"] == []


def test_opening_balance_write_persists():
    company = _company({})
    db = _fake_db(company)
    svc = OpeningBalanceService(db, integration=MagicMock())
    asyncio.run(svc.set_filed_net_assets(uuid4(), "2023-09-30", Decimal("324")))
    assert company.audit_config["opening_balance"]["filed"]["2023-09-30"] == "324"
    asyncio.run(svc.dismiss(uuid4(), "2023-09-30"))
    assert "2023-09-30" in company.audit_config["opening_balance"]["dismissed"]
    db.commit.assert_awaited()


# ---------------- Bank Balance ----------------

def test_bank_balance_manual_statement_vs_tb():
    company = _company({"bank_balance": {"statement": {"090": {"2026-03-31": "64749.69"}}}})
    integ = MagicMock()
    integ.fetch_chart_of_accounts = AsyncMock(return_value=[
        {"AccountID": "acc090", "Code": "090", "Name": "Business", "Type": "BANK"}])
    integ.fetch_trial_balance = AsyncMock(return_value=_tb("acc090", "93360.82"))
    svc = BankBalanceService(_fake_db(company), integration=integ)

    out = asyncio.run(svc.list_differences(uuid4(), "2026-03-31"))
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert item["per_xero_tb"] == 93360.82
    assert item["per_bank_statement"] == 64749.69
    assert item["per_xero_statement"] is None       # Finance API gated
    assert round(item["difference"], 2) == -28611.13
    assert round(out["total_value"], 2) == 28611.13
    assert item["process_url"]                       # Xero deep link present


def test_bank_balance_excluded_account_hidden():
    company = _company({"bank_balance": {
        "statement": {"090": {"2026-03-31": "64749.69"}}, "excluded": ["090"]}})
    integ = MagicMock()
    integ.fetch_chart_of_accounts = AsyncMock(return_value=[
        {"AccountID": "acc090", "Code": "090", "Name": "Business", "Type": "BANK"}])
    integ.fetch_trial_balance = AsyncMock(return_value=_tb("acc090", "93360.82"))
    svc = BankBalanceService(_fake_db(company), integration=integ)
    assert asyncio.run(svc.list_differences(uuid4(), "2026-03-31"))["items"] == []


def test_bank_balance_marked_ok_not_flagged_but_shown_with_all():
    company = _company({"bank_balance": {
        "statement": {"090": {"2026-03-31": "64749.69"}}, "marked_ok": ["090|2026-03-31"]}})
    integ = MagicMock()
    integ.fetch_chart_of_accounts = AsyncMock(return_value=[
        {"AccountID": "acc090", "Code": "090", "Name": "Business", "Type": "BANK"}])
    integ.fetch_trial_balance = AsyncMock(return_value=_tb("acc090", "93360.82"))
    svc = BankBalanceService(_fake_db(company), integration=integ)
    # marked-ok → not flagged → hidden unless show_all, and never in the total
    assert asyncio.run(svc.list_differences(uuid4(), "2026-03-31"))["items"] == []
    shown = asyncio.run(svc.list_differences(uuid4(), "2026-03-31", show_all=True))
    assert shown["items"][0]["marked_ok"] is True
    assert shown["total_value"] == 0.0


def test_bank_balance_write_persists():
    company = _company({})
    db = _fake_db(company)
    svc = BankBalanceService(db, integration=MagicMock())
    asyncio.run(svc.set_statement_balance(uuid4(), "090", "2026-03-31", Decimal("64749.69")))
    assert company.audit_config["bank_balance"]["statement"]["090"]["2026-03-31"] == "64749.69"
    asyncio.run(svc.mark_ok(uuid4(), "090", "2026-03-31", ok=True))
    assert "090|2026-03-31" in company.audit_config["bank_balance"]["marked_ok"]
    db.commit.assert_awaited()
