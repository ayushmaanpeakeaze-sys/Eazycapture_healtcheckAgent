"""Shared Groq AsyncGroq client.

Importing this module is cheap — the client is created lazily on first
``get_groq()`` call. Both ``ai_service`` and ``enrichment_service`` use the
same instance so we have one connection pool and one retry budget across
the process.
"""
from __future__ import annotations

from typing import Optional

from groq import AsyncGroq

from app.core.config import settings

# A few retries absorb 429 bursts; kept modest so an unreachable Groq fails fast
# and degrades to the deterministic checks rather than dragging out the audit.
_GROQ_MAX_RETRIES = 2
# Hard per-request timeout (seconds) so a stalled connection can't block the audit.
_GROQ_TIMEOUT_S = 20.0

_client: Optional[AsyncGroq] = None


def get_groq() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            max_retries=_GROQ_MAX_RETRIES,
            timeout=_GROQ_TIMEOUT_S,
        )
    return _client


async def close_groq() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
