"""Pydantic schemas for the RBAC / auth surface."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["admin", "team_member"]
UserStatus = Literal["invited", "active", "disabled"]
AccessMode = Literal["all", "selected"]

# Lightweight email format check (no email-validator dependency). Catches
# malformed input — empty, missing @, no domain dot, spaces. It does NOT
# (and can't cheaply) verify the domain actually accepts mail, so a
# valid-format but undeliverable address still sends and may bounce.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if not _EMAIL_RE.match(cleaned):
        raise ValueError("Enter a valid email address.")
    return cleaned


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------

class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    """Self-service signup: creates a new firm with this user as its admin."""

    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=256)
    full_name: Optional[str] = Field(default=None, max_length=255)
    firm_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _normalize_email(v)


class RequestOtpRequest(RegisterRequest):
    """Step 1 of email-verified signup — same fields as register; we hold them
    until the emailed code is confirmed."""


class OtpRequestedResponse(BaseModel):
    email: str
    expires_in_seconds: int
    email_sent: bool


class VerifyOtpRequest(BaseModel):
    """Step 2 — the code from the email creates the firm + admin."""

    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=4, max_length=8)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _normalize_email(v)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    user_id: UUID
    email: str


class AcceptInviteRequest(BaseModel):
    invite_token: str = Field(..., min_length=10, max_length=128)
    password: str = Field(..., min_length=8, max_length=256)
    full_name: Optional[str] = Field(default=None, max_length=255)


class InviteInfoResponse(BaseModel):
    """Public lookup for the accept-invite page — lets it show 'Set your
    password for john@firm.com' before the user submits. ``email`` is only
    revealed when the token matches a real invite (whoever holds the secret
    token already received it at that address)."""
    valid: bool                        # token matches an open, unexpired invite
    email: Optional[str] = None
    full_name: Optional[str] = None
    expired: bool = False
    reason: Optional[str] = None       # human message when not valid


# ---------- Admin: invite + manage ----------

class InviteRequest(BaseModel):
    email: str = Field(..., max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=255)
    # "all"      → access every company, including future ones
    # "selected" → only the company_ids below
    access_mode: AccessMode = "selected"
    # Company UUIDs the new team member can access. Used only when
    # access_mode == "selected".
    company_ids: list[UUID] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _normalize_email(v)


class InviteResponse(BaseModel):
    user_id: UUID
    email: str
    role: Role
    status: UserStatus
    access_mode: AccessMode
    invite_token: str          # frontend builds the accept-invite link from this
    # Ready-made accept-invite link (APP_BASE_URL + path + token) so the
    # frontend's "copy link" doesn't have to assemble the URL itself.
    accept_url: Optional[str] = None
    invite_expires_at: datetime
    assigned_company_ids: list[UUID] = Field(default_factory=list)
    # Delivery outcome of the invite notification. ``email_sent`` is False
    # (with ``email_channel="console"``) when SMTP isn't configured — the
    # invite still exists; the link was only logged, not emailed.
    email_sent: bool = False
    email_channel: str = "console"
    email_error: Optional[str] = None


class RemoveResponse(BaseModel):
    id: UUID
    removed: bool = True


class AssignCompaniesRequest(BaseModel):
    # "all"      → access every company, including future ones
    # "selected" → only the company_ids below
    access_mode: AccessMode = "selected"
    company_ids: list[UUID] = Field(default_factory=list)


class UserSummary(_Base):
    id: UUID
    email: str
    full_name: Optional[str] = None
    role: Role
    status: UserStatus
    access_mode: AccessMode = "selected"
    created_at: datetime
    assigned_company_ids: list[UUID] = Field(default_factory=list)
    # Last email delivery status: sent | delivered | bounced | complained |
    # failed | None. UI shows a on bounced/complained (bad address).
    email_status: Optional[str] = None


class UserListResponse(BaseModel):
    users: list[UserSummary] = Field(default_factory=list)
    total: int = 0


class MeResponse(BaseModel):
    user_id: Optional[UUID] = None
    email: str
    role: Role
    access_mode: AccessMode = "selected"
    assigned_company_ids: list[UUID] = Field(default_factory=list)
    firm_id: Optional[UUID] = None
    firm_name: Optional[str] = None
