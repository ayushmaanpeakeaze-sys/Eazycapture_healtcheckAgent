"""Lazy async Redis client singleton.

Importing this module is cheap; the connection pool is only created on first
``get_redis()`` call. Decodes responses to ``str`` so callers work with plain
JSON strings.
"""
from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis, from_url

from app.core.config import settings

_client: Optional[Redis] = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
