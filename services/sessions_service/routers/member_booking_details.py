"""Member-owned booking views for quarter attendance and fee settlement."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionBooking

router = APIRouter(prefix="/sessions/bookings")


def booking_details(booking, session) -> dict:
    return {
        "id": str(booking.id),
        "session_id": str(session.id),
        "session_title": session.title,
        "session_starts_at": session.starts_at,
        "location_name": session.location_name,
        "status": booking.status.value,
        "session_status": session.status.value,
        "booking_source": booking.booking_source,
        "access_source": booking.access_source,
        "fee_amount_kobo": booking.fee_amount_kobo,
        "settled": bool(
            booking.payment_intent_id
            or booking.wallet_transaction_id
            or booking.fee_amount_kobo == 0
        ),
    }


@router.get("/me/club-quarter")
async def my_quarter_bookings(
    user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)
):
    rows = (
        await db.execute(
            select(SessionBooking, Session)
            .join(Session, Session.id == SessionBooking.session_id)
            .where(
                SessionBooking.member_auth_id == user.user_id,
                or_(
                    SessionBooking.booking_source == "club_quarter",
                    SessionBooking.access_source == "quarterly_prepaid",
                ),
                Session.starts_at > utc_now(),
            )
            .order_by(Session.starts_at)
        )
    ).all()
    return [booking_details(booking, session) for booking, session in rows]


@router.get("/{booking_id}/settlement")
async def my_booking_settlement(
    booking_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    row = (
        await db.execute(
            select(SessionBooking, Session)
            .join(Session, Session.id == SessionBooking.session_id)
            .where(
                SessionBooking.id == booking_id,
                SessionBooking.member_auth_id == user.user_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Booking not found for your account")
    return booking_details(*row)
