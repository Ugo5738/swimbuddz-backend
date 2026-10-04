"""One operational roster for members, attached guests and self-paying guests."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_members_bulk, internal_get, internal_post
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingGuest,
    GuestPass,
    Session,
    SessionBooking,
    SessionParticipant,
    SessionRate,
)
from services.sessions_service.schemas.guest_pass import (
    SessionRosterEntry,
    SessionRosterResponse,
)
from services.sessions_service.schemas.participant import (
    AdminGuestWalkInCreate,
    SessionParticipantResponse,
)
from services.sessions_service.services.guest_identity import normalize_guest_phone

router = APIRouter(tags=["session-roster"])


async def _sync_participant_attendance(
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    notes: str | None,
) -> bool:
    try:
        response = await internal_post(
            service_url=get_settings().ATTENDANCE_SERVICE_URL,
            path=f"/internal/attendance/session/{session_id}/participant",
            calling_service="sessions",
            json={
                "participant_id": str(participant_id),
                "status": "present",
                "notes": notes or "Admin guest walk-in",
            },
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def _participant_response(
    participant: SessionParticipant, *, attendance_recorded: bool
) -> SessionParticipantResponse:
    return SessionParticipantResponse(
        id=participant.id,
        session_id=participant.session_id,
        participant_kind=participant.participant_kind,
        source=participant.source,
        full_name=participant.full_name_snapshot,
        email=participant.email_snapshot,
        phone=participant.phone_snapshot,
        fee_amount_kobo=participant.fee_amount_kobo,
        rate_code=participant.rate_code,
        payment_status=participant.payment_status,
        waiver_status=participant.waiver_status,
        attendance_recorded=attendance_recorded,
    )


@router.post(
    "/admin/sessions/{session_id}/walk-in-guests",
    response_model=SessionParticipantResponse,
    status_code=201,
)
async def create_guest_walk_in(
    session_id: uuid.UUID,
    body: AdminGuestWalkInCreate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Record an unregistered person who actually attended a session.

    This is deliberately not a GuestPass: no booking, waiver acceptance or
    payment is fabricated after the fact. It creates an auditable participant,
    snapshots the commercial amount owed, and best-effort records attendance.
    """
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    phone = normalize_guest_phone(body.phone) if body.phone else None
    email = str(body.email).lower() if body.email else None

    # Retrying the same retrospective entry should sync attendance rather than
    # creating another human record.
    existing_query = select(SessionParticipant).where(
        SessionParticipant.session_id == session_id,
        SessionParticipant.source == "walk_in",
        SessionParticipant.participant_kind == "guest",
    )
    if phone:
        existing_query = existing_query.where(
            SessionParticipant.phone_snapshot == phone
        )
    elif email:
        existing_query = existing_query.where(
            SessionParticipant.email_snapshot == email
        )
    else:
        existing_query = existing_query.where(
            SessionParticipant.full_name_snapshot == body.full_name
        )
    existing = (await db.execute(existing_query)).scalars().first()
    if existing is not None:
        recorded = await _sync_participant_attendance(
            session_id, existing.id, existing.notes
        )
        return _participant_response(existing, attendance_recorded=recorded)

    rate = (
        await db.execute(
            select(SessionRate).where(
                SessionRate.session_id == session_id,
                SessionRate.audience == "guest",
            )
        )
    ).scalar_one_or_none()
    configured_fee = int(
        rate.amount_kobo
        if rate is not None
        else (
            session.guest_fee_kobo
            if session.guest_fee_kobo is not None
            else session.pool_fee or 0
        )
    )
    fee = configured_fee if body.fee_amount_kobo is None else body.fee_amount_kobo
    if fee != configured_fee and not body.fee_override_reason:
        raise HTTPException(
            status_code=422,
            detail="Explain why this walk-in uses a fee different from the configured guest rate.",
        )

    note_parts = [body.notes]
    if body.fee_override_reason:
        note_parts.append(f"Fee override: {body.fee_override_reason}")
    participant = SessionParticipant(
        session_id=session_id,
        participant_kind="guest",
        source="walk_in",
        full_name_snapshot=body.full_name,
        email_snapshot=email,
        phone_snapshot=phone,
        rate_id=rate.id if rate else None,
        rate_code="guest",
        fee_amount_kobo=fee,
        payment_status=body.payment_status,
        # Retrospective admin entry must never manufacture waiver acceptance.
        waiver_status="missing",
        notes=" | ".join(part for part in note_parts if part) or None,
        created_by=admin.user_id,
    )
    db.add(participant)
    await db.commit()
    await db.refresh(participant)

    recorded = await _sync_participant_attendance(
        session_id, participant.id, participant.notes
    )
    return _participant_response(participant, attendance_recorded=recorded)


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
                    or_(
                        SessionBooking.status == "confirmed",
                        and_(
                            SessionBooking.status == "pending",
                            or_(
                                SessionBooking.expires_at.is_(None),
                                SessionBooking.expires_at > utc_now(),
                            ),
                        ),
                    ),
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
    participants = list(
        (
            await db.execute(
                select(SessionParticipant)
                .where(
                    SessionParticipant.session_id == session_id,
                    SessionParticipant.source == "walk_in",
                )
                .order_by(SessionParticipant.created_at)
            )
        ).scalars()
    )
    passes = list(
        (
            await db.execute(
                select(GuestPass)
                .where(
                    GuestPass.session_id == session_id,
                    or_(
                        GuestPass.status.in_(["confirmed", "attended"]),
                        and_(
                            GuestPass.status == "pending_payment",
                            GuestPass.booking_mode == "reservation",
                            GuestPass.reservation_expires_at > utc_now(),
                        ),
                    ),
                )
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
    by_participant = {
        str(a["participant_id"]): a
        for a in attendance
        if a.get("participant_id")
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
            id=p.id,
            kind="walk_in_guest",
            full_name=p.full_name_snapshot,
            booking_status="walk_in",
            attendance_status=by_participant.get(str(p.id), {}).get("status"),
            phone=p.phone_snapshot,
            fee_amount_kobo=p.fee_amount_kobo,
            payment_status=p.payment_status,
            waiver_status=p.waiver_status,
        )
        for p in participants
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
