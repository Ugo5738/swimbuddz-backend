"""Capacity and reservation timing shared by session booking workflows."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionBookingStatus,
)

PENDING_TTL_MINUTES = 15


async def assert_booking_capacity(
    db: AsyncSession,
    *,
    session: Session,
    member_id: uuid.UUID,
    new_party_size: int,
) -> None:
    """Lock a session and reject a booking that exceeds head-count capacity."""
    await db.execute(
        select(Session.id).where(Session.id == session.id).with_for_update()
    )
    now = utc_now()
    used = (
        await db.execute(
            select(func.coalesce(func.sum(SessionBooking.party_size), 0)).where(
                SessionBooking.session_id == session.id,
                SessionBooking.member_id != member_id,
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
    ).scalar_one()
    if int(used) + new_party_size > session.capacity:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This session is full - {int(used)}/{session.capacity} seats "
                f"taken and you need {new_party_size}."
            ),
        )
