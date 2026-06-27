"""Tests for the production-hardening layer:

* production config guard (refuse insecure prod boot)
* login brute-force rate limiting (fail counter + reset)
* token revocation (disabled account rejected immediately)
"""
from __future__ import annotations

import uuid
from dataclasses import replace

import httpx
import pytest

from app.core.config import assert_safe_for_environment, settings
from app.main import app


# --------------------------------------------------------------------------
# Production config guard
# --------------------------------------------------------------------------

def _cfg(**overrides):
    # Settings is a frozen dataclass → replace() returns a patched copy.
    return replace(settings, **overrides)


def test_prod_guard_blocks_auth_disabled():
    with pytest.raises(RuntimeError):
        assert_safe_for_environment(
            _cfg(APP_ENV="production", AUTH_DISABLED=True, JWT_SECRET="x" * 40)
        )


def test_prod_guard_blocks_weak_secret():
    with pytest.raises(RuntimeError):
        assert_safe_for_environment(
            _cfg(APP_ENV="production", AUTH_DISABLED=False, JWT_SECRET="change-me")
        )


def test_prod_guard_allows_strong_config():
    # Should NOT raise.
    assert_safe_for_environment(
        _cfg(APP_ENV="production", AUTH_DISABLED=False, JWT_SECRET="A" * 40)
    )


def test_guard_noop_in_development():
    # Insecure settings are fine outside production.
    assert_safe_for_environment(
        _cfg(APP_ENV="development", AUTH_DISABLED=True, JWT_SECRET="")
    )


# --------------------------------------------------------------------------
# Login rate limiting (deterministic — fake in-memory Redis)
# --------------------------------------------------------------------------

class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, k):
        return self.store.get(k)

    async def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    async def expire(self, k, s):
        self.ttls[k] = s
        return True

    async def ttl(self, k):
        return self.ttls.get(k, -1)

    async def delete(self, k):
        self.store.pop(k, None)
        self.ttls.pop(k, None)
        return 1


async def test_login_lockout_after_max_failures(monkeypatch):
    from app.core import rate_limit

    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    email = "brute@firm.com"

    # Up to the limit: never blocked, each failure recorded.
    for _ in range(settings.LOGIN_MAX_FAILURES):
        blocked, _ = await rate_limit.is_login_blocked(email)
        assert blocked is False
        await rate_limit.record_login_failure(email)

    # One past the limit → blocked with a positive retry-after.
    blocked, retry_after = await rate_limit.is_login_blocked(email)
    assert blocked is True
    assert retry_after >= 1

    # A successful login resets the counter.
    await rate_limit.reset_login_failures(email)
    blocked, _ = await rate_limit.is_login_blocked(email)
    assert blocked is False


async def test_rate_limit_fails_open_when_redis_down(monkeypatch):
    from app.core import rate_limit

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit, "get_redis", _boom)
    # Must not raise, must allow (fail open).
    blocked, retry_after = await rate_limit.is_login_blocked("x@y.com")
    assert blocked is False and retry_after == 0


# --------------------------------------------------------------------------
# Token revocation — a disabled account's valid token is rejected
# --------------------------------------------------------------------------

@pytest.fixture
async def async_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as ac:
        yield ac


async def test_disabled_user_token_is_rejected(async_client: httpx.AsyncClient):
    from app.core.db import SyncSessionLocal
    from app.core.security import create_access_token
    from app.modules.auth.models import User
    from sqlalchemy import delete

    uid = uuid.uuid4()
    email = f"revoked_{uid.hex[:8]}@firm.com"
    with SyncSessionLocal() as db:
        db.add(User(
            id=uid, email=email, role="team_member",
            status="disabled", company_access_mode="all",
        ))
        db.commit()

    orig_secret, orig_disabled = settings.JWT_SECRET, settings.AUTH_DISABLED
    object.__setattr__(settings, "JWT_SECRET", "test-secret-123")
    object.__setattr__(settings, "AUTH_DISABLED", False)
    try:
        # Token is valid (signed with the active secret), but the account is
        # disabled → must be rejected immediately, not honoured until expiry.
        token = create_access_token(user_id=uid, email=email, role="team_member")
        resp = await async_client.get(
            "/api/v1/health/companies-panorama/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, resp.text
        assert "disabled" in resp.json()["detail"].lower()
    finally:
        object.__setattr__(settings, "JWT_SECRET", orig_secret)
        object.__setattr__(settings, "AUTH_DISABLED", orig_disabled)
        with SyncSessionLocal() as db:
            db.execute(delete(User).where(User.id == uid))
            db.commit()


async def test_active_user_token_is_accepted(async_client: httpx.AsyncClient):
    from app.core.db import SyncSessionLocal
    from app.core.security import create_access_token
    from app.modules.auth.models import User
    from sqlalchemy import delete

    uid = uuid.uuid4()
    email = f"active_{uid.hex[:8]}@firm.com"
    with SyncSessionLocal() as db:
        db.add(User(
            id=uid, email=email, role="team_member",
            status="active", company_access_mode="all",
        ))
        db.commit()

    orig_secret, orig_disabled = settings.JWT_SECRET, settings.AUTH_DISABLED
    object.__setattr__(settings, "JWT_SECRET", "test-secret-123")
    object.__setattr__(settings, "AUTH_DISABLED", False)
    try:
        token = create_access_token(user_id=uid, email=email, role="team_member")
        resp = await async_client.get(
            "/api/v1/health/companies-panorama/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        object.__setattr__(settings, "JWT_SECRET", orig_secret)
        object.__setattr__(settings, "AUTH_DISABLED", orig_disabled)
        with SyncSessionLocal() as db:
            db.execute(delete(User).where(User.id == uid))
            db.commit()
