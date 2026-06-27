"""Password hashing + JWT issuing for the RBAC layer.

This service issues its OWN tokens — it is independent of the main
EazyCapture app. ``JWT_SECRET`` signs the tokens this service mints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import bcrypt
import jwt

from app.core.config import settings

# Access-token lifetime — configurable via JWT_TTL_HOURS (default 12h).
_TOKEN_TTL_HOURS = settings.JWT_TTL_HOURS or 12
# Invite links live for 7 days.
INVITE_TTL_DAYS = 7


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Bcrypt hash a plaintext password. Returns a utf-8 string."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    """Constant-time check of a plaintext password against a stored hash."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT issuing
# ---------------------------------------------------------------------------

def create_access_token(
    *,
    user_id: UUID | str,
    email: str,
    role: str,
    ttl_hours: int = _TOKEN_TTL_HOURS,
) -> str:
    """Mint a signed JWT carrying the user's identity + role.

    Claims: ``sub`` (user id), ``email``, ``role``, ``exp``, ``iat``.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl_hours)).timestamp()),
    }
    secret = settings.JWT_SECRET or "dev-insecure-secret-change-me"
    return jwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM or "HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode + verify a JWT. Raises ``jwt.PyJWTError`` on any problem."""
    secret = settings.JWT_SECRET or "dev-insecure-secret-change-me"
    return jwt.decode(
        token, secret, algorithms=[settings.JWT_ALGORITHM or "HS256"],
    )


# ---------------------------------------------------------------------------
# Invite tokens
# ---------------------------------------------------------------------------

def generate_invite_token() -> str:
    """A URL-safe random token for invite links."""
    import secrets
    return secrets.token_urlsafe(32)
