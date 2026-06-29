"""Lightweight login brute-force protection backed by Redis.

Counts FAILED login attempts per key (email) in a fixed window. A
successful login resets the counter, so legitimate users are never locked
out by their own successful logins — only repeated failures trip the lock.

Fails OPEN: if Redis is unavailable the checks allow the request (with a
logged warning) rather than locking everyone out on an infra hiccup.
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger("eazycapture.rate_limit")

_PREFIX = "login_fail:"


async def is_login_blocked(identifier: str) -> tuple[bool, int]:
    """Return (blocked, retry_after_seconds). Blocked once failures reach
    ``LOGIN_MAX_FAILURES`` within the window."""
    key = f"{_PREFIX}{identifier}"
    try:
        redis = get_redis()
        count = int(await redis.get(key) or 0)
        if count >= settings.LOGIN_MAX_FAILURES:
            ttl = await redis.ttl(key)
            return True, max(int(ttl), 1)
        return False, 0
    except Exception as exc:  # noqa: BLE001 — fail open on infra issues
        logger.warning("rate-limit check skipped (redis unavailable): %s", exc)
        return False, 0


async def record_login_failure(identifier: str) -> None:
    """Increment the failure counter, (re)setting the window TTL on first hit."""
    key = f"{_PREFIX}{identifier}"
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.LOGIN_FAILURE_WINDOW_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate-limit record skipped (redis unavailable): %s", exc)


async def reset_login_failures(identifier: str) -> None:
    """Clear the counter after a successful login."""
    try:
        await get_redis().delete(f"{_PREFIX}{identifier}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate-limit reset skipped (redis unavailable): %s", exc)
