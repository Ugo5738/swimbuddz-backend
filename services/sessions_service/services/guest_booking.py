"""Guest admission, lifecycle and private receipt capabilities."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_event_session_contract, internal_get
from services.sessions_service.models import GuestBookingGrant, GuestPass, Session

SAFETY_ACKNOWLEDGEMENT_VERSION = "pool-safety-2026-09"
SAFETY_ACKNOWLEDGEMENT_TEXT = (
    "I confirm my details are accurate and agree to follow pool safety instructions "
    "and the SwimBuddz Club Standards."
)
GUEST_PASS_RESERVATION_MINUTES = 30


def lifecycle_mode(
    session: Session, *, now: datetime | None = None, has_grant: bool = False
) -> str:
    """Post-start settlement never creates a capacity reservation."""
    now = now or utc_now()
    if session.status not in {"scheduled", "in_progress", "completed"}:
        return "closed"
    if (
        not session.allows_guests
        or session.guest_booking_mode == "disabled"
        or session.guest_fee_kobo is None
    ):
        return "closed"
    if now < session.starts_at:
        if session.status != "scheduled":
            return "closed"
        cutoff = session.guest_booking_closes_at or session.starts_at
        return "reservation" if now < cutoff else "closed"
    deadline = session.ends_at + timedelta(days=session.guest_reconciliation_days)
    if has_grant or (session.guest_reconciliation_days > 0 and now <= deadline):
        return "settlement"
    return "closed"


def receipt_token(guest_pass_id: uuid.UUID) -> str:
    # Domain-separated HMAC cannot be used as an auth JWT. No guest identity is
    # encoded in the token; changing the server key invalidates old capabilities.
    secret = get_settings().SUPABASE_JWT_SECRET.encode()
    return hmac.new(
        secret, f"guest-pass-receipt:v1:{guest_pass_id}".encode(), hashlib.sha256
    ).hexdigest()


def has_receipt_access(guest_pass_id: uuid.UUID, token: str | None) -> bool:
    return bool(
        token
        and token.isascii()
        and hmac.compare_digest(receipt_token(guest_pass_id), token)
    )


def receipt_url(guest_pass_id: uuid.UUID) -> str:
    return f"{get_settings().FRONTEND_URL.rstrip('/')}/guest-pass/{guest_pass_id}#token={receipt_token(guest_pass_id)}"


async def resolve_grant(
    db: AsyncSession, session_id: uuid.UUID, token: str | None, *, lock: bool = False
) -> GuestBookingGrant | None:
    if not token:
        return None
    query = select(GuestBookingGrant).where(
        GuestBookingGrant.session_id == session_id,
        GuestBookingGrant.token_hash == hashlib.sha256(token.encode()).hexdigest(),
    )
    if lock:
        query = query.with_for_update()
    grant = (await db.execute(query)).scalar_one_or_none()
    if (
        not grant
        or grant.revoked_at
        or grant.expires_at <= utc_now()
        or grant.used_by_pass_id
    ):
        raise HTTPException(
            status_code=403,
            detail="This guest approval link has expired, was used, or was revoked. Contact SwimBuddz.",
        )
    return grant


async def event_guest_context(session: Session, *, has_grant: bool = False) -> dict:
    """Events remain authoritative; fail closed if their policy is unavailable."""
    if not session.event_id:
        return {}
    try:
        event = await get_event_session_contract(
            str(session.event_id), calling_service="sessions"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="Session details are temporarily unavailable"
        ) from exc
    if not event or event.get("status") not in {"published", "completed"}:
        raise HTTPException(status_code=404, detail="This guest swim is unavailable")
    if (
        event.get("visibility") != "public" or event.get("tier_access") == "invite_only"
    ) and not has_grant:
        raise HTTPException(
            status_code=403,
            detail="This event requires an individual guest approval link from SwimBuddz",
        )
    return event


def public_location(
    session: Session, event: dict, *, confirmed_private: bool = False
) -> tuple[str | None, str | None]:
    private = session.guest_location_private or event.get("is_location_private", False)
    if private and not confirmed_private:
        return event.get("location_area"), None
    return event.get("location_name") or session.location_name, session.location_address


async def member_guest_url(session: Session, member_auth_id: str) -> str | None:
    if (
        lifecycle_mode(session) == "closed"
        or session.guest_booking_mode == "approval_required"
    ):
        return None
    # Invite-only Events require an Admin-issued, email-bound guest approval.
    try:
        await event_guest_context(session)
    except HTTPException:
        return None
    response = await internal_get(
        service_url=get_settings().WALLET_SERVICE_URL,
        path=f"/internal/wallet/referral-link/{member_auth_id}",
        calling_service="sessions",
        timeout=10,
    )
    response.raise_for_status()
    code = parse_qs(urlparse(response.json()["share_link"]).query).get("ref", [None])[0]
    if not code:
        raise ValueError("Referral link did not contain a code")
    query = urlencode({"ref": code, "source": "member_share"})
    return f"{get_settings().FRONTEND_URL.rstrip('/')}/guest-pass/session/{session.id}?{query}"


def require_admission(
    session: Session,
    *,
    referrer_auth_id: str | None,
    grant: GuestBookingGrant | None,
    email: str,
) -> str:
    mode = lifecycle_mode(session, has_grant=grant is not None)
    if mode == "closed":
        raise HTTPException(
            status_code=422,
            detail="Guest booking is closed. Contact SwimBuddz for help or an individual reconciliation link.",
        )
    if grant and grant.email != email.lower().strip():
        raise HTTPException(
            status_code=403, detail="Use the email address approved for this guest link"
        )
    if (
        session.guest_booking_mode == "member_invite"
        and not referrer_auth_id
        and not grant
    ):
        raise HTTPException(
            status_code=403,
            detail="A valid member invitation is required for this swim",
        )
    if session.guest_booking_mode == "approval_required" and not grant:
        raise HTTPException(
            status_code=403,
            detail="An individual guest approval link from SwimBuddz is required",
        )
    return mode


async def public_receipt(
    guest_pass: GuestPass, session: Session, *, private: bool = False
) -> dict:
    from services.sessions_service.schemas.guest_pass import GuestPassPublicResponse

    # Public receipt IDs alone never disclose a private venue. Event policy is
    # read again so changing privacy after checkout also takes effect here.
    event = {}
    if session.event_id:
        event = await get_event_session_contract(
            str(session.event_id), calling_service="sessions"
        )
        if not event:
            raise HTTPException(
                status_code=503, detail="Session details are temporarily unavailable"
            )
    location, address = public_location(
        session,
        event,
        confirmed_private=private and guest_pass.status in {"confirmed", "attended"},
    )
    return (
        GuestPassPublicResponse.model_validate(guest_pass)
        .model_copy(
            update={
                "session_title": session.title,
                "starts_at": session.starts_at,
                "ends_at": session.ends_at,
                "timezone": session.timezone,
                "location_name": location,
                "location_address": address,
                "attendance_recorded": guest_pass.attended_at is not None,
            }
        )
        .model_dump()
    )
