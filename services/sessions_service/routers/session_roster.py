"""One operational roster for members, attached guests and self-paying guests."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import get_members_bulk, internal_get
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingGuest,
    GuestPass,
    Session,
    SessionBooking,
)
from services.sessions_service.schemas.guest_pass import (
    SessionRosterEntry,
    SessionRosterResponse,
)

router = APIRouter(tags=["session-roster"])


@router.get("/admin/sessions/{session_id}/roster", response_model=SessionRosterResponse)
async def session_roster(
    session_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if await db.get(Session, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    bookings = list(
        (
            await db.execute(
                select(SessionBooking)
                .where(
                    SessionBooking.session_id == session_id,
                    SessionBooking.status.in_(["pending", "confirmed"]),
                )
                .order_by(SessionBooking.booked_at)
            )
        ).scalars()
    )
    attached = (
        list(
            (
                await db.execute(
                    select(BookingGuest).where(
                        BookingGuest.booking_id.in_([b.id for b in bookings])
                    )
                )
            ).scalars()
        )
        if bookings
        else []
    )
    passes = list(
        (
            await db.execute(
                select(GuestPass)
                .where(GuestPass.session_id == session_id)
                .order_by(GuestPass.created_at)
            )
        ).scalars()
    )
    attendance_available = True
    attendance = []
    try:
        response = await internal_get(
            service_url=get_settings().ATTENDANCE_SERVICE_URL,
            path=f"/attendance/sessions/{session_id}/attendance",
            calling_service="sessions",
        )
        response.raise_for_status()
        attendance = response.json()
    except httpx.HTTPError:
        attendance_available = False
    member_ids = list(
        dict.fromkeys(
            [str(b.member_id) for b in bookings]
            + [str(row["member_id"]) for row in attendance if row.get("member_id")]
        )
    )
    try:
        members = {
            str(m["id"]): m
            for m in await get_members_bulk(member_ids, calling_service="sessions")
        }
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="Roster names are temporarily unavailable"
        ) from exc
    names = {
        key: " ".join(p for p in [m.get("first_name"), m.get("last_name")] if p)
        or "Member"
        for key, m in members.items()
    }
    by_member = {str(a["member_id"]): a for a in attendance if a.get("member_id")}
    by_guest = {
        str(a["booking_guest_id"]): a for a in attendance if a.get("booking_guest_id")
    }
    by_booking = {b.id: b for b in bookings}
    entries = [
        SessionRosterEntry(
            id=b.id,
            kind="member",
            full_name=names.get(str(b.member_id), "Member"),
            booking_status=b.status,
            attendance_status=by_member.get(str(b.member_id), {}).get("status"),
        )
        for b in bookings
    ]
    booked_members = {str(b.member_id) for b in bookings}
    entries.extend(
        SessionRosterEntry(
            id=a["id"],
            kind="member",
            full_name=names.get(str(a["member_id"]), "Member"),
            booking_status="walk_in",
            attendance_status=a.get("status"),
        )
        for a in attendance
        if a.get("member_id") and str(a["member_id"]) not in booked_members
    )
    entries.extend(
        SessionRosterEntry(
            id=g.id,
            kind="booking_guest",
            full_name=g.full_name or "Guest name required before check-in",
            booking_status=by_booking[g.booking_id].status,
            attendance_status=by_guest.get(str(g.id), {}).get("status"),
            inviter=names.get(
                str(by_booking[g.booking_id].member_id), "Member booking"
            ),
            phone=g.phone,
        )
        for g in attached
    )
    entries.extend(
        SessionRosterEntry(
            id=g.id,
            kind="guest_pass",
            full_name=g.full_name,
            booking_status=g.status,
            attendance_status="present" if g.attended_at else None,
            inviter=f"Referral {g.referral_code}" if g.referral_code else None,
            booking_mode=g.booking_mode,
            phone=g.phone,
            actual_swim_minutes=g.actual_swim_minutes,
        )
        for g in passes
    )
    return SessionRosterResponse(
        entries=entries, attendance_available=attendance_available
    )
