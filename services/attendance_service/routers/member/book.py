"""Member-facing SessionBooking endpoints.

POST /sessions/{session_id}/book      — member self-book ahead
POST /bookings/{booking_id}/cancel    — member or admin cancel before session

A booked row starts at SessionBookingStatus.PENDING. The corporate-wellness
or payment-intent flow transitions it to CONFIRMED. At session time the
existing sign-in endpoint links the booking to the new AttendanceRecord
via ``booking_id``. The nightly NO_SHOW sweep produces ABSENT
AttendanceRecords for confirmed bookings whose session ended without
check-in.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_session_by_id
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import (
    BookingChannel,
    MemberRef,
    SessionBooking,
    SessionBookingStatus,
)
from services.attendance_service.schemas import (
    SessionBookingCreate,
    SessionBookingResponse,
)

from ._shared import get_current_member, validate_session_access

router = APIRouter()


@router.post(
    "/sessions/{session_id}/book",
    response_model=SessionBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def book_session(
    session_id: uuid.UUID,
    booking_in: SessionBookingCreate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Pre-book a session as the authenticated member.

    Creates a SessionBooking(status=PENDING). Payment is handled
    out-of-band — payments_service writes ``payment_intent_id`` and
    transitions the booking to CONFIRMED when the payment clears.
    """
    if booking_in.session_id != session_id:
        raise HTTPException(
            status_code=422,
            detail="booking session_id does not match URL session_id",
        )

    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    await validate_session_access(session_data, str(current_member.id))

    # Idempotency: if there's already a PENDING or CONFIRMED booking for
    # this (session, member), return it instead of creating a duplicate.
    # A CANCELLED row stays in the table; the unique constraint blocks a
    # second insert, so re-booking after cancel requires admin work today.
    existing_q = select(SessionBooking).where(
        SessionBooking.session_id == session_id,
        SessionBooking.member_id == current_member.id,
    )
    existing = (await db.execute(existing_q)).scalar_one_or_none()
    if existing is not None:
        if existing.status in (
            SessionBookingStatus.PENDING,
            SessionBookingStatus.CONFIRMED,
        ):
            return existing
        # CANCELLED / EXPIRED — block; admin must re-issue.
        raise HTTPException(
            status_code=409,
            detail=(
                f"A previous booking for this session exists with "
                f"status={existing.status.value}. Contact support to re-book."
            ),
        )

    booking = SessionBooking(
        session_id=session_id,
        member_id=current_member.id,
        member_auth_id=current_member.auth_id,
        status=SessionBookingStatus.PENDING,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=booking_in.fee_amount_kobo,
        notes=booking_in.notes,
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=SessionBookingResponse,
)
async def cancel_booking(
    booking_id: uuid.UUID,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel a booking before the session starts.

    Members can only cancel their own bookings. Refund handling lives
    in payments_service per the booking's ``payment_intent_id`` /
    ``wallet_transaction_id`` — this endpoint only transitions status
    and timestamps.
    """
    q = select(SessionBooking).where(SessionBooking.id == booking_id)
    booking = (await db.execute(q)).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.member_id != current_member.id:
        raise HTTPException(
            status_code=403,
            detail="You can only cancel your own bookings.",
        )
    if booking.status in (
        SessionBookingStatus.CANCELLED,
        SessionBookingStatus.EXPIRED,
    ):
        # Idempotent — return the row as-is.
        return booking
    if booking.status == SessionBookingStatus.CONFIRMED:
        # Check whether session has already started; if so, refuse —
        # at that point the booking is past its useful life and the
        # nightly sweep will produce an AttendanceRecord(status=ABSENT).
        session_data = await get_session_by_id(
            str(booking.session_id), calling_service="attendance"
        )
        if session_data:
            try:
                from datetime import datetime as _dt

                starts = _dt.fromisoformat(
                    session_data["starts_at"].replace("Z", "+00:00")
                )
                if starts <= utc_now():
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Cannot cancel a booking after its session has started. "
                            "The booking will be marked NO_SHOW if no attendance "
                            "is recorded."
                        ),
                    )
            except (KeyError, ValueError):
                # If we can't parse the timestamp, fall through — better
                # to allow cancel than reject on missing data.
                pass

    booking.status = SessionBookingStatus.CANCELLED
    booking.cancelled_at = utc_now()
    await db.commit()
    await db.refresh(booking)
    return booking
