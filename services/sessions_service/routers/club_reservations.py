"""Service-owned reservations for the exact swims sold in a prepaid quarter."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
    ClubSessionHold,
)
from services.sessions_service.services.booking_attendance import (
    sync_booking_attendance,
)
from services.sessions_service.services.booking_capacity import assert_booking_capacity

router = APIRouter(
    prefix="/internal/sessions/club-reservations",
    dependencies=[Depends(require_service_role)],
)


class PrepaidReservationsRequest(BaseModel):
    payment_reference: str | None = None
    require_holds: bool = False
    enrollment_id: uuid.UUID
    club_id: uuid.UUID
    member_id: uuid.UUID
    member_auth_id: str = Field(min_length=1)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    session_ids: list[uuid.UUID] = Field(max_length=260)


@router.post("")
async def reserve_prepaid_swims(
    body: PrepaidReservationsRequest, db: AsyncSession = Depends(get_async_db)
):
    now = utc_now()
    # Lock in stable order, shared with regular and guest capacity checks.
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(body.session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(sessions) != len(set(body.session_ids)):
        raise HTTPException(
            409, "A purchased Club swim is missing; reconcile the quarter"
        )
    confirmed = []
    created = 0
    holds = (
        {
            row.session_id: row
            for row in (
                await db.execute(
                    select(ClubSessionHold)
                    .where(
                        ClubSessionHold.payment_reference == body.payment_reference,
                        ClubSessionHold.session_id.in_(body.session_ids),
                        ClubSessionHold.member_id == body.member_id,
                        ClubSessionHold.club_id == body.club_id,
                    )
                    .with_for_update()
                )
            ).scalars()
        }
        if body.payment_reference
        else {}
    )
    for session in sessions:
        hold = holds.get(session.id)
        if (
            session.session_type != SessionType.CLUB
            or session.club_id != body.club_id
            or session.club_access_mode != "plan_included"
        ):
            raise HTTPException(
                409, "Purchased swim does not belong to this Club quarter"
            )
        # Only future purchased inclusions inside the enrollment's access dates.
        if (
            session.starts_at <= now
            or not body.starts_at <= session.starts_at < body.ends_at
            or session.status == SessionStatus.CANCELLED
        ):
            if hold and hold.status != "released":
                hold.status = "consumed"
            continue
        if session.status != SessionStatus.SCHEDULED:
            raise HTTPException(
                409, "A purchased swim is not published; reconcile the quarter"
            )
        booking = (
            await db.execute(
                select(SessionBooking)
                .where(
                    SessionBooking.session_id == session.id,
                    SessionBooking.member_id == body.member_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if booking and booking.status == SessionBookingStatus.CANCELLED:
            if hold:
                hold.status = "consumed"
            continue  # Never undo an explicit attendance cancellation on replay.
        if booking and booking.status == SessionBookingStatus.CONFIRMED:
            if hold:
                hold.status = "consumed"
            confirmed.append(booking)
            continue
        if body.require_holds and (not hold or hold.status != "protected"):
            raise HTTPException(
                409,
                "Paid Club checkout is missing its protected swim reservation; reconcile its capacity record",
            )
        if booking and (
            booking.payment_intent_id
            or booking.wallet_transaction_id
            or booking.party_size != 1
            or (
                booking.status == SessionBookingStatus.PENDING
                and (booking.expires_at is None or booking.expires_at > now)
            )
        ):
            raise HTTPException(
                409,
                "An existing booking has a payment or guest reservation; reconcile it before auto-reserving",
            )
        if not hold or hold.status != "protected":
            # Historical purchases have no hold. Their explicit backfill still
            # checks capacity; new checkouts consume seats committed before pay.
            await assert_booking_capacity(
                db, session=session, member_id=body.member_id, new_party_size=1
            )
        if booking is None:
            booking = SessionBooking(
                id=uuid.uuid5(body.enrollment_id, str(session.id)),
                session_id=session.id,
                member_id=body.member_id,
                member_auth_id=body.member_auth_id,
            )
        booking.status = SessionBookingStatus.CONFIRMED
        booking.channel = BookingChannel.MEMBER_SELF
        booking.party_size = 1
        booking.fee_amount_kobo = booking.member_fee_amount_kobo = 0
        booking.access_source, booking.booking_source = (
            "quarterly_prepaid",
            "club_quarter",
        )
        booking.confirmed_at, booking.booked_at = now, now
        booking.expires_at = booking.cancelled_at = None
        db.add(booking)
        if hold:
            hold.status = "consumed"
        confirmed.append(booking)
        created += 1
    await db.commit()
    # Propagate failures to the paid-entitlement retry mechanism. Bookings are
    # already durable and the next attempt also repairs attendance idempotently.
    semaphore = asyncio.Semaphore(8)

    async def sync(booking):
        async with semaphore:
            await sync_booking_attendance(booking)

    results = await asyncio.gather(
        *(sync(booking) for booking in confirmed), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return {"created": created, "confirmed": len(confirmed)}
