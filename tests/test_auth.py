"""Optional JWT auth tests.

We don't want to leave ``JWT_SECRET`` permanently set in the test
process (every other test would need a token). Each test patches
``settings.JWT_SECRET`` and ``settings.AUTH_DISABLED`` for its scope
only, asserts the dependency behaves, then unwinds.
"""
from __future__ import annotations

import uuid

import httpx
import jwt
import pytest

from app.core.config import settings
from app.main import app


@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


async def test_auth_skipped_when_secret_unset(async_client: httpx.AsyncClient):
    """POC default: empty secret → no Authorization header needed."""
    original_secret = settings.JWT_SECRET
    original_auth_disabled = settings.AUTH_DISABLED
    object.__setattr__(settings, "JWT_SECRET", "")
    object.__setattr__(settings, "AUTH_DISABLED", False)
    try:
        resp = await async_client.get("/api/v1/health/companies-panorama/")
        # 200 OK = the auth dep returned None and let the request through.
        assert resp.status_code == 200, resp.text
    finally:
        object.__setattr__(settings, "JWT_SECRET", original_secret)
        object.__setattr__(settings, "AUTH_DISABLED", original_auth_disabled)


async def test_auth_rejects_bad_token_when_secret_set(
    async_client: httpx.AsyncClient,
):
    """Strict mode: bad token → 401 from the dependency."""
    # ``Settings`` is a frozen dataclass — patch the attribute by
    # rebinding it on the live instance via object.__setattr__.
    original_secret = settings.JWT_SECRET
    original_auth_disabled = settings.AUTH_DISABLED
    object.__setattr__(settings, "JWT_SECRET", "test-secret-123")
    object.__setattr__(settings, "AUTH_DISABLED", False)
    try:
        # Missing header → 401.
        no_header = await async_client.get(
            "/api/v1/health/companies-panorama/",
        )
        assert no_header.status_code == 401
        assert "Missing or malformed" in no_header.json()["detail"]

        # Bad token → 401.
        bad_token = await async_client.get(
            "/api/v1/health/companies-panorama/",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert bad_token.status_code == 401
        assert "Invalid token" in bad_token.json()["detail"]

        # Good token → 200. Cross-check the same dependency now opens.
        token = jwt.encode(
            {"user_id": str(uuid.uuid4()), "account_id": "demo"},
            "test-secret-123",
            algorithm="HS256",
        )
        good = await async_client.get(
            "/api/v1/health/companies-panorama/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert good.status_code == 200, good.text
    finally:
        object.__setattr__(settings, "JWT_SECRET", original_secret)
        object.__setattr__(settings, "AUTH_DISABLED", original_auth_disabled)


async def test_auth_skipped_when_demo_override_enabled(
    async_client: httpx.AsyncClient,
):
    """Demo override: AUTH_DISABLED=true skips auth even if a secret exists."""
    original_secret = settings.JWT_SECRET
    original_auth_disabled = settings.AUTH_DISABLED
    object.__setattr__(settings, "JWT_SECRET", "test-secret-123")
    object.__setattr__(settings, "AUTH_DISABLED", True)
    try:
        resp = await async_client.get("/api/v1/health/companies-panorama/")
        assert resp.status_code == 200, resp.text
    finally:
        object.__setattr__(settings, "JWT_SECRET", original_secret)
        object.__setattr__(settings, "AUTH_DISABLED", original_auth_disabled)
