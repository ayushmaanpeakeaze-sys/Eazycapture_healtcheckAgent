"""Audit Configuration screen — per-client settings round-trip.

PUT /audit-config/ must persist the per-client thresholds (sanitised), GET must
return them alongside the defaults, and a bad value must be dropped rather than
poison the stored config. This closes the loop on Phase 1: the engine consumes
``audit_config['settings']``; these endpoints are how the frontend writes it.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.db import SyncSessionLocal
from app.main import app
from app.modules.healthcheck.models import Company

_BASE = "/api/v1/health/audit-config/"


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


def _insert_company(name: str) -> uuid.UUID:
    cid = uuid.uuid4()
    with SyncSessionLocal() as db:
        db.add(Company(id=cid, name=name, is_active=True))
        db.commit()
    return cid


def _delete_company(cid: uuid.UUID) -> None:
    with SyncSessionLocal() as db:
        c = db.get(Company, cid)
        if c is not None:
            db.delete(c)
            db.commit()


def _read_cfg(cid: uuid.UUID) -> dict:
    with SyncSessionLocal() as db:
        return dict((db.get(Company, cid).audit_config) or {})


async def test_put_persists_settings_and_get_returns_them(async_client):
    co = _insert_company("Cfg settings test")
    try:
        resp = await async_client.put(
            f"{_BASE}?company_id={co}",
            json={
                "disabled_rules": [],
                "settings": {"overdue_days": 30, "outlier_multiple": "2.5",
                             "misallocated_vague_codes": ["500", "501"]},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["settings"]["overdue_days"] == 30
        assert body["settings"]["outlier_multiple"] == "2.5"          # Decimal → str
        assert body["settings"]["misallocated_vague_codes"] == ["500", "501"]
        # defaults are exposed for the form to render placeholders
        assert body["settings_defaults"]["overdue_days"] == 60

        # persisted to the DB JSONB blob
        assert _read_cfg(co)["settings"]["overdue_days"] == 30

        # GET round-trips the same values
        got = (await async_client.get(f"{_BASE}?company_id={co}")).json()
        assert got["settings"]["overdue_days"] == 30
        assert got["settings"]["outlier_multiple"] == "2.5"
    finally:
        _delete_company(co)


async def test_put_drops_bad_and_unknown_keys(async_client):
    co = _insert_company("Cfg bad-value test")
    try:
        resp = await async_client.put(
            f"{_BASE}?company_id={co}",
            json={
                "disabled_rules": [],
                "settings": {"overdue_days": "not-a-number",   # bad → dropped
                             "not_a_setting": 5,               # unknown → dropped
                             "credit_age_days": 45},           # good → kept
            },
        )
        assert resp.status_code == 200, resp.text
        settings = resp.json()["settings"]
        assert "overdue_days" not in settings        # bad value NOT persisted as default
        assert "not_a_setting" not in settings
        assert settings["credit_age_days"] == 45
    finally:
        _delete_company(co)


async def test_get_returns_duplicate_invoice_settings_schema(async_client):
    co = _insert_company("Cfg schema test")
    try:
        got = (await async_client.get(f"{_BASE}?company_id={co}")).json()
        schema = got["settings_schema"]
        assert isinstance(schema, list) and schema
        # The Duplicate Invoices check renders its own section, field metadata
        # + defaults included, so the settings UI needs no hardcoding.
        dup = next(e for e in schema if e["check"] == "duplicate_invoice")
        assert dup["group"] == "Duplicates"
        by_key = {f["key"]: f for f in dup["fields"]}
        # The 4 toggles + the Confidence bar are exposed.
        assert {"duplicate_days_window", "duplicate_require_same_amount",
                "duplicate_require_exact_reference", "duplicate_also_check_paid",
                "duplicate_min_confidence"} <= set(by_key)
        assert by_key["duplicate_min_confidence"]["type"] == "percent"
        assert by_key["duplicate_days_window"]["type"] == "int"
        assert by_key["duplicate_days_window"]["default"] == 0      # same day by default
        assert by_key["duplicate_require_same_amount"]["type"] == "bool"
    finally:
        _delete_company(co)


async def test_put_without_settings_keeps_other_config(async_client):
    co = _insert_company("Cfg no-settings test")
    try:
        resp = await async_client.put(
            f"{_BASE}?company_id={co}",
            json={"disabled_rules": ["anomaly"], "ignore_before": "2026-01-01"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["disabled_rules"] == ["anomaly"]
        assert body["ignore_before"] == "2026-01-01"
        assert body["settings"] == {}            # none set → empty, not error
    finally:
        _delete_company(co)
