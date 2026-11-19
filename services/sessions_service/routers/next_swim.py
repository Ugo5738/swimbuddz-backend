"""Backend-owned next-swim discovery. Read-only: a recommendation is not a hold.

The Sessions service is authoritative for session access, not browser rankings.
Checkout still performs fresh eligibility, capacity, and payment validation.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
)
from services.sessions_service.routers.member import _decorate_sessions_for_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _ranking_key(row: dict) -> tuple:
    """Prefer regular home-Club inclusions, then imminence.

    Keep this deterministic and strictly subordinate to backend access.bookable.
    An explicit future preference model can refine ties without granting access.
    """
    access = row.get("access") or {}
    source = str(access.get("access_source") or "")
    price = access.get("fee_amount_kobo")
    preferred = 0 if source in {"plan_included", "active_club", "home_club"} else 1
    included = 0 if price == 0 else 1
    return (
        preferred,
        included,
        str(row.get("starts_at") or ""),
        str(row.get("id") or ""),
    )


@router.get("/me/next-swim")
async def get_next_swim(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the next eligible, unbooked swim and possible alternatives.

    The endpoint never creates a booking, sells a seat, or assumes an Academy
    class requires separate payment. Results are advisory until checkout.
    """
    if is_admin_or_service(user):
        raise HTTPException(403, "Use a member account to book your next swim")
    try:
        member = await get_member_by_auth_id(user.user_id, calling_service="sessions")
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Could not verify member profile") from exc
    if not member or not member.get("id"):
        raise HTTPException(403, "Complete your member profile first")
    try:
        member_id = uuid.UUID(str(member["id"]))
    except ValueError as exc:
        raise HTTPException(502, "Member profile identifier is invalid") from exc

    now = utc_now()
    # Bounded discovery so the endpoint does not scan years of scheduled rows.
    sessions = (
        (
            await db.execute(
                select(Session)
                .where(
                    Session.starts_at > now,
                    Session.starts_at < now + timedelta(days=90),
                    Session.status == SessionStatus.SCHEDULED,
                )
                .order_by(Session.starts_at.asc(), Session.id.asc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    if not sessions:
        return {"primary": None, "alternatives": [], "next_confirmed": None}

    confirmed = set(
        (
            await db.execute(
                select(SessionBooking.session_id).where(
                    SessionBooking.member_id == member_id,
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                    SessionBooking.session_id.in_([session.id for session in sessions]),
                )
            )
        )
        .scalars()
        .all()
    )

    # Uses the existing batched cross-service membership, Club, cohort, pod
    # and Event access checks; do not recreate entitlement logic here.
    decorated = await _decorate_sessions_for_user(list(sessions), user, db)
    confirmed_rows = [
        row for row in decorated if uuid.UUID(str(row["id"])) in confirmed
    ]
    bookable = [
        row
        for row in decorated
        if uuid.UUID(str(row["id"])) not in confirmed
        and (row.get("access") or {}).get("bookable") is True
    ]
    bookable.sort(key=_ranking_key)
    confirmed_rows.sort(key=lambda row: str(row.get("starts_at") or ""))
    return {
        "primary": bookable[0] if bookable else None,
        "alternatives": bookable[1:6],
        "next_confirmed": confirmed_rows[0] if confirmed_rows else None,
    }
