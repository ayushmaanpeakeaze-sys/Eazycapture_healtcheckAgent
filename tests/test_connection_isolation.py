"""Nango connection lookup is scoped by end-user (firm isolation).

``find_live_xero_connection`` must never return a connection belonging to a
different end-user, even when that other connection is newer — otherwise one
firm could adopt another firm's Xero org during connect / self-heal.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.modules.integrations.nango.service import NangoService


def _service(connections: list[dict]) -> NangoService:
    client = MagicMock()
    client._is_enabled.return_value = True
    client._base_url = "https://api.nango.dev"
    client._secret_key = "test-secret"
    client._send = AsyncMock(return_value={"connections": connections})
    client.get_connection = AsyncMock(
        return_value={"connection_config": {"tenant_id": "tenant-X"}}
    )
    return NangoService(client=client)


def _conns() -> list[dict]:
    pck = settings.NANGO_XERO_INTEGRATION_ID
    return [
        {"connection_id": "conn-A", "provider_config_key": pck,
         "created": "2026-06-01T00:00:00Z", "end_user": {"id": "firmA-user"}},
        # Firm B's connection is NEWER — the global-newest pick would return this.
        {"connection_id": "conn-B", "provider_config_key": pck,
         "created": "2026-06-28T00:00:00Z", "end_user": {"id": "firmB-user"}},
    ]


async def test_unscoped_returns_newest():
    svc = _service(_conns())
    result = await svc.find_live_xero_connection()
    assert result is not None and result[0] == "conn-B"


async def test_scoped_to_firm_a_never_returns_firm_b():
    svc = _service(_conns())
    result = await svc.find_live_xero_connection(end_user_id="firmA-user")
    assert result is not None and result[0] == "conn-A"  # NOT the newer conn-B


async def test_scoped_to_firm_b_returns_its_own():
    svc = _service(_conns())
    result = await svc.find_live_xero_connection(end_user_id="firmB-user")
    assert result is not None and result[0] == "conn-B"


async def test_scoped_to_stranger_returns_none():
    svc = _service(_conns())
    assert await svc.find_live_xero_connection(end_user_id="someone-else") is None
