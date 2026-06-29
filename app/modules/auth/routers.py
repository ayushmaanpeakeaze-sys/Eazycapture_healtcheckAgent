"""Auth + RBAC HTTP surface, mounted at ``/api/v1/auth``.

Two roles only — admin and team_member:

* ``POST   /auth/login``                — email + password → JWT.
* ``POST   /auth/accept-invite``        — invite token + password → activate → JWT.
* ``GET    /auth/me``                   — current user + their company assignments.
* ``POST   /auth/invite``               — admin invites a team member (emails them).
* ``GET    /auth/users``                — admin lists all users.
* ``PUT    /auth/users/{id}/companies`` — admin sets a team member's companies.
* ``POST   /auth/users/{id}/disable``   — admin disables a user (revokes access).
* ``POST   /auth/users/{id}/enable``    — admin re-enables a disabled user.
* ``POST   /auth/users/{id}/resend-invite`` — admin re-sends an invite email.
* ``DELETE /auth/users/{id}``           — admin removes a user entirely.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_admin
from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import (
    is_login_blocked,
    record_login_failure,
    reset_login_failures,
)
from app.core.security import (
    INVITE_TTL_DAYS,
    create_access_token,
    generate_invite_token,
    hash_password,
    verify_password,
)
from app.modules.auth.schemas import (
    AcceptInviteRequest,
    AssignCompaniesRequest,
    InviteInfoResponse,
    InviteRequest,
    InviteResponse,
    LoginRequest,
    MeResponse,
    OtpRequestedResponse,
    RegisterRequest,
    RemoveResponse,
    RequestOtpRequest,
    TokenResponse,
    UserListResponse,
    UserSummary,
    VerifyOtpRequest,
)
from app.core.redis_client import get_redis
from app.modules.auth.models import Firm, User, UserCompanyAccess
from app.modules.healthcheck.models import Company
from app.modules.notifications import DeliveryResult, Recipient, notification_service
from app.modules.notifications.persistence import record_send
from app.modules.notifications.templates import invite_email, signup_otp_email

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _assigned_company_ids(db: AsyncSession, user_id: UUID) -> list[UUID]:
    rows = await db.execute(
        select(UserCompanyAccess.company_id).where(
            UserCompanyAccess.user_id == user_id,
        )
    )
    return [r[0] for r in rows.all()]


async def _validate_company_ids(
    db: AsyncSession, company_ids: list[UUID], firm_id: UUID | None = None,
) -> None:
    """Raise 400 if any id isn't a company the firm owns. When ``firm_id`` is
    given, a company outside the firm reads as 'unknown' (never revealed)."""
    if not company_ids:
        return
    q = select(Company.id).where(Company.id.in_(company_ids))
    if firm_id is not None:
        q = q.where(Company.firm_id == firm_id)
    rows = await db.execute(q)
    found = {r[0] for r in rows.all()}
    missing = [str(c) for c in company_ids if c not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown company id(s): {', '.join(missing)}",
        )


async def _firm_id_for(db: AsyncSession, user_id: UUID | None) -> UUID | None:
    """The firm a user belongs to (None for the script-created super-admin)."""
    if user_id is None:
        return None
    return (
        await db.execute(select(User.firm_id).where(User.id == user_id))
    ).scalar_one_or_none()


async def _load_managed_user(db: AsyncSession, user_id: UUID, admin: CurrentUser) -> User:
    """Load a user the admin is allowed to manage. A user in another firm reads
    as 404 (never revealed). A firm-less super-admin can manage anyone."""
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    admin_firm = await _firm_id_for(db, admin.user_id)
    if admin_firm is not None and user.firm_id != admin_firm:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def _accept_url(token: str) -> str:
    """Frontend accept-invite link the email points at. Built from
    APP_BASE_URL + ACCEPT_INVITE_PATH so it tracks wherever the UI lives."""
    base = settings.APP_BASE_URL.rstrip("/")
    path = settings.ACCEPT_INVITE_PATH
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}?token={token}"


async def _send_invite_email(
    db: AsyncSession,
    *,
    user_id: UUID,
    to_email: str,
    full_name: str | None,
    token: str,
    inviter_email: str,
    kind: str = "invite",
) -> DeliveryResult:
    """Send the invite via the notification service and log the attempt to
    notification_log (+ mirror status onto User.email_status). Never raises —
    a failed send returns ok=False so the invite (already committed) still
    stands and the admin can resend."""
    message = invite_email(
        accept_url=_accept_url(token),
        expires_days=INVITE_TTL_DAYS,
        inviter_email=inviter_email or None,
    )
    delivery = await notification_service.send(
        recipient=Recipient(email=to_email, name=full_name),
        message=message,
    )
    await record_send(
        db, recipient_email=to_email, kind=kind, delivery=delivery, user_id=user_id,
    )
    return delivery


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    email = payload.email.strip().lower()

    # Brute-force guard: block after too many recent failures for this email.
    blocked, retry_after = await is_login_blocked(email)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if (
        user is None
        or user.status != "active"
        or not verify_password(payload.password, user.password_hash)
    ):
        # Count the failure (drives the lock) — same generic message either way.
        await record_login_failure(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Success clears the counter so a legitimate user is never locked out.
    await reset_login_failures(email)
    token = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return TokenResponse(
        access_token=token, role=user.role, user_id=user.id, email=user.email,
    )


# ---------------------------------------------------------------------------
# Register (self-service signup → new firm + its first admin)
# ---------------------------------------------------------------------------

_OTP_TTL_SECONDS = 600          # codes are valid for 10 minutes
_OTP_MAX_ATTEMPTS = 5           # wrong tries before the code is burned
_OTP_KEY = "signup:otp:{email}"


async def _email_taken(db: AsyncSession, email: str) -> bool:
    return (
        await db.execute(select(User.id).where(User.email == email))
    ).scalar_one_or_none() is not None


async def _create_firm_and_admin(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str,
    full_name: str | None,
    firm_name: str | None,
) -> User:
    """Create a workspace and its first (admin) user. The caller has already
    confirmed the email is free."""
    name = (firm_name or "").strip() or f"{email.split('@')[0]}'s workspace"
    firm = Firm(name=name)
    db.add(firm)
    await db.flush()  # firm.id
    user = User(
        firm_id=firm.id,
        email=email,
        full_name=(full_name or "").strip() or None,
        role="admin",
        status="active",
        company_access_mode="all",
        password_hash=password_hash,
    )
    db.add(user)
    await db.commit()
    return user


def _token_for(user: User) -> TokenResponse:
    token = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return TokenResponse(
        access_token=token, role=user.role, user_id=user.id, email=user.email,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Create a new firm (workspace) + its first admin, then sign them in.

    Direct (unverified) signup. The email-verified flow is
    ``/register/request-otp`` → ``/register/verify``; this stays for
    programmatic use.
    """
    email = payload.email.strip().lower()
    if await _email_taken(db, email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user = await _create_firm_and_admin(
        db,
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        firm_name=payload.firm_name,
    )
    return _token_for(user)


@router.post("/register/request-otp", response_model=OtpRequestedResponse)
async def register_request_otp(
    payload: RequestOtpRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OtpRequestedResponse:
    """Step 1 of email-verified signup: email a 6-digit code and hold the
    (hashed) signup details in Redis until the code is confirmed."""
    email = payload.email.strip().lower()
    if await _email_taken(db, email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    record = {
        "code": code,
        "password_hash": hash_password(payload.password),
        "full_name": payload.full_name,
        "firm_name": payload.firm_name,
        "attempts": 0,
    }
    await redis.set(_OTP_KEY.format(email=email), json.dumps(record), ex=_OTP_TTL_SECONDS)

    delivery = await notification_service.send(
        recipient=Recipient(email=email, name=payload.full_name),
        message=signup_otp_email(code=code, expires_minutes=_OTP_TTL_SECONDS // 60),
    )
    return OtpRequestedResponse(
        email=email, expires_in_seconds=_OTP_TTL_SECONDS, email_sent=delivery.ok,
    )


@router.post("/register/verify", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_verify(
    payload: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    """Step 2: a matching code creates the firm + admin and signs them in."""
    email = payload.email.strip().lower()
    key = _OTP_KEY.format(email=email)
    raw = await redis.get(key)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your code has expired. Please start signup again.",
        )
    record = json.loads(raw)

    if record.get("attempts", 0) >= _OTP_MAX_ATTEMPTS:
        await redis.delete(key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect codes. Please start signup again.",
        )

    if payload.code.strip() != record.get("code"):
        record["attempts"] = record.get("attempts", 0) + 1
        await redis.set(key, json.dumps(record), keepttl=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect code. Please try again.",
        )

    # The email may have been registered between step 1 and step 2.
    if await _email_taken(db, email):
        await redis.delete(key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = await _create_firm_and_admin(
        db,
        email=email,
        password_hash=record["password_hash"],
        full_name=record.get("full_name"),
        firm_name=record.get("firm_name"),
    )
    await redis.delete(key)
    return _token_for(user)


# ---------------------------------------------------------------------------
# Accept invite
# ---------------------------------------------------------------------------

@router.post("/accept-invite", response_model=TokenResponse)
async def accept_invite(
    payload: AcceptInviteRequest, db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = (
        await db.execute(
            select(User).where(User.invite_token == payload.invite_token.strip())
        )
    ).scalar_one_or_none()

    if user is None or user.status != "invited":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or already-used invite.",
        )
    if user.invite_expires_at and user.invite_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite has expired. Ask an admin to re-invite you.",
        )

    user.password_hash = hash_password(payload.password)
    if payload.full_name:
        user.full_name = payload.full_name.strip()
    user.status = "active"
    user.invite_token = None
    user.invite_expires_at = None
    await db.commit()

    token = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return TokenResponse(
        access_token=token, role=user.role, user_id=user.id, email=user.email,
    )


# ---------------------------------------------------------------------------
# Invite info (public — for the accept-invite page to show the email)
# ---------------------------------------------------------------------------

@router.get("/invite-info", response_model=InviteInfoResponse)
async def invite_info(
    token: str, db: AsyncSession = Depends(get_db),
) -> InviteInfoResponse:
    """Look up an invite token so the accept-invite page can display which
    email it's for, before the user sets a password. Public (the invitee
    isn't logged in yet). Never reveals an email for a token that doesn't
    match a real invite."""
    token = (token or "").strip()
    if not token:
        return InviteInfoResponse(
            valid=False, reason="This invite link is invalid or has already been used.",
        )

    user = (
        await db.execute(select(User).where(User.invite_token == token))
    ).scalar_one_or_none()

    if user is None:
        return InviteInfoResponse(
            valid=False, reason="This invite link is invalid or has already been used.",
        )
    if user.status != "invited":
        return InviteInfoResponse(
            valid=False, email=user.email,
            reason="This invite has already been accepted. Please sign in instead.",
        )
    if user.invite_expires_at and user.invite_expires_at < datetime.now(timezone.utc):
        return InviteInfoResponse(
            valid=False, expired=True, email=user.email, full_name=user.full_name,
            reason="This invite has expired. Ask an admin to re-invite you.",
        )
    return InviteInfoResponse(
        valid=True, email=user.email, full_name=user.full_name,
    )


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
async def me(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    access_mode = "all" if user.is_admin else "selected"
    company_ids: list[UUID] = []
    firm_id: UUID | None = None
    firm_name: str | None = None
    if user.user_id is not None:
        db_user = (
            await db.execute(select(User).where(User.id == user.user_id))
        ).scalar_one_or_none()
        if db_user is not None:
            firm_id = db_user.firm_id
            if not user.is_admin:
                access_mode = db_user.company_access_mode
        if firm_id is not None:
            firm_name = (
                await db.execute(select(Firm.name).where(Firm.id == firm_id))
            ).scalar_one_or_none()
        if not user.is_admin and access_mode != "all":
            company_ids = await _assigned_company_ids(db, user.user_id)
    return MeResponse(
        user_id=user.user_id,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        access_mode=access_mode,  # type: ignore[arg-type]
        assigned_company_ids=company_ids,
        firm_id=firm_id,
        firm_name=firm_name,
    )


# ---------------------------------------------------------------------------
# Admin: invite a team member
# ---------------------------------------------------------------------------

@router.post("/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_team_member(
    payload: InviteRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    email = payload.email.strip().lower()

    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    # Invitee joins the admin's firm; only that firm's companies are assignable.
    firm_id = await _firm_id_for(db, admin.user_id)
    # "all" mode ignores the company list (flag-based access to everything).
    selected_ids = [] if payload.access_mode == "all" else payload.company_ids
    await _validate_company_ids(db, selected_ids, firm_id)

    invite_token = generate_invite_token()
    expires = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    user = User(
        firm_id=firm_id,
        email=email,
        full_name=(payload.full_name or "").strip() or None,
        role="team_member",
        status="invited",
        company_access_mode=payload.access_mode,
        invite_token=invite_token,
        invite_expires_at=expires,
        invited_by=admin.user_id,
    )
    db.add(user)
    await db.flush()  # get user.id

    for cid in selected_ids:
        db.add(UserCompanyAccess(user_id=user.id, company_id=cid))
    await db.commit()

    # Send the invite email *after* commit so a flaky SMTP never rolls back
    # the invite. Delivery outcome is surfaced in the response + logged.
    delivery = await _send_invite_email(
        db,
        user_id=user.id,
        to_email=user.email,
        full_name=user.full_name,
        token=invite_token,
        inviter_email=admin.email,
    )

    return InviteResponse(
        user_id=user.id,
        email=user.email,
        role="team_member",
        status="invited",
        access_mode=payload.access_mode,
        invite_token=invite_token,
        accept_url=_accept_url(invite_token),
        invite_expires_at=expires,
        assigned_company_ids=selected_ids,
        email_sent=delivery.ok,
        email_channel=delivery.channel,
        email_error=None if delivery.ok else delivery.detail,
    )


# ---------------------------------------------------------------------------
# Admin: list users
# ---------------------------------------------------------------------------

@router.get("/users", response_model=UserListResponse)
async def list_users(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    admin_firm = await _firm_id_for(db, admin.user_id)
    q = select(User).order_by(User.created_at.asc())
    if admin_firm is not None:
        q = q.where(User.firm_id == admin_firm)
    users = (await db.execute(q)).scalars().all()

    summaries: list[UserSummary] = []
    for u in users:
        cids = await _assigned_company_ids(db, u.id)
        summaries.append(UserSummary(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,  # type: ignore[arg-type]
            status=u.status,  # type: ignore[arg-type]
            access_mode=u.company_access_mode,  # type: ignore[arg-type]
            created_at=u.created_at,
            assigned_company_ids=cids,
            email_status=u.email_status,
        ))
    return UserListResponse(users=summaries, total=len(summaries))


# ---------------------------------------------------------------------------
# Admin: set a team member's company assignments (replaces the set)
# ---------------------------------------------------------------------------

@router.put("/users/{user_id}/companies", response_model=UserSummary)
async def set_user_companies(
    user_id: UUID,
    payload: AssignCompaniesRequest,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserSummary:
    user = await _load_managed_user(db, user_id, admin)
    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins already have access to every company.",
        )

    # "all" mode ignores the company list and grants access to everything.
    selected_ids = [] if payload.access_mode == "all" else payload.company_ids
    await _validate_company_ids(db, selected_ids, user.firm_id)

    user.company_access_mode = payload.access_mode

    # Replace the whole assignment set (cleared entirely in "all" mode).
    existing = (
        await db.execute(
            select(UserCompanyAccess).where(UserCompanyAccess.user_id == user_id)
        )
    ).scalars().all()
    for row in existing:
        await db.delete(row)
    for cid in selected_ids:
        db.add(UserCompanyAccess(user_id=user_id, company_id=cid))
    await db.commit()

    return UserSummary(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,  # type: ignore[arg-type]
        status=user.status,  # type: ignore[arg-type]
        access_mode=payload.access_mode,
        created_at=user.created_at,
        assigned_company_ids=selected_ids,
        email_status=user.email_status,
    )


# ---------------------------------------------------------------------------
# Admin: disable a user
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/disable", response_model=UserSummary)
async def disable_user(
    user_id: UUID,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserSummary:
    user = await _load_managed_user(db, user_id, admin)
    if user.id == admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot disable your own account.",
        )
    user.status = "disabled"
    await db.commit()
    return await _to_summary(db, user)


async def _to_summary(db: AsyncSession, user: User) -> UserSummary:
    """Build a UserSummary including the user's company assignments."""
    cids = await _assigned_company_ids(db, user.id)
    return UserSummary(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,  # type: ignore[arg-type]
        status=user.status,  # type: ignore[arg-type]
        access_mode=user.company_access_mode,  # type: ignore[arg-type]
        created_at=user.created_at,
        assigned_company_ids=cids,
        email_status=user.email_status,
    )


# ---------------------------------------------------------------------------
# Admin: re-enable a disabled user
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/enable", response_model=UserSummary)
async def enable_user(
    user_id: UUID,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserSummary:
    user = await _load_managed_user(db, user_id, admin)
    if user.status == "invited":
        # Invited users have no password yet — enabling would skip the
        # invite flow. The admin should resend the invite instead.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This user hasn't accepted their invite yet. Resend the invite instead.",
        )
    user.status = "active"  # idempotent if already active
    await db.commit()
    return await _to_summary(db, user)


# ---------------------------------------------------------------------------
# Admin: resend an invite email (regenerates the token + extends expiry)
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/resend-invite", response_model=InviteResponse)
async def resend_invite(
    user_id: UUID,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    user = await _load_managed_user(db, user_id, admin)
    if user.status != "invited":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This user has already accepted their invite.",
        )

    # Fresh token + new expiry so an old/leaked link can't be reused.
    user.invite_token = generate_invite_token()
    user.invite_expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    await db.commit()

    delivery = await _send_invite_email(
        db,
        user_id=user.id,
        to_email=user.email,
        full_name=user.full_name,
        token=user.invite_token,
        inviter_email=admin.email,
        kind="resend_invite",
    )
    cids = await _assigned_company_ids(db, user.id)
    return InviteResponse(
        user_id=user.id,
        email=user.email,
        role="team_member",
        status="invited",
        access_mode=user.company_access_mode,  # type: ignore[arg-type]
        invite_token=user.invite_token,
        accept_url=_accept_url(user.invite_token),
        invite_expires_at=user.invite_expires_at,
        assigned_company_ids=cids,
        email_sent=delivery.ok,
        email_channel=delivery.channel,
        email_error=None if delivery.ok else delivery.detail,
    )


# ---------------------------------------------------------------------------
# Admin: remove a user entirely (cascades company assignments)
# ---------------------------------------------------------------------------

@router.delete("/users/{user_id}", response_model=RemoveResponse)
async def remove_user(
    user_id: UUID,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RemoveResponse:
    user = await _load_managed_user(db, user_id, admin)
    if user.id == admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own account.",
        )
    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot be removed.",
        )
    # user_company_access rows cascade via ON DELETE CASCADE.
    await db.delete(user)
    await db.commit()
    return RemoveResponse(id=user_id, removed=True)
