"""Shared capacity predicate, including durable provider-exposed Club seats."""

from sqlalchemy import and_, exists, func, or_, select

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    ClubSessionHold,
    SessionBooking,
    SessionBookingStatus,
)


def live_club_hold(at=None):
    return or_(
        ClubSessionHold.status == "protected",
        and_(
            ClubSessionHold.status == "active",
            ClubSessionHold.expires_at > (at or utc_now()),
        ),
    )


async def held_club_seats(db, session_id, exclude_member=None) -> int:
    now = utc_now()
    # An already booked member occupies one seat, not both a booking and a hold.
    booked = exists(
        select(SessionBooking.id).where(
            SessionBooking.session_id == ClubSessionHold.session_id,
            SessionBooking.member_id == ClubSessionHold.member_id,
            or_(
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
                and_(
                    SessionBooking.status == SessionBookingStatus.PENDING,
                    or_(
                        SessionBooking.expires_at.is_(None),
                        SessionBooking.expires_at > now,
                    ),
                ),
            ),
        )
    )
    query = select(func.count(func.distinct(ClubSessionHold.member_id))).where(
        ClubSessionHold.session_id == session_id, live_club_hold(now), ~booked
    )
    if exclude_member is not None:
        query = query.where(ClubSessionHold.member_id != exclude_member)
    return int((await db.execute(query)).scalar_one())
