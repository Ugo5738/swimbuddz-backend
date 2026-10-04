"""Canonical SessionParticipant reconciliation.

This module lets bookings, guest passes and true door walk-ins converge on one
human-per-session representation without deleting any of the legacy records.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    BookingGuest,
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionParticipant,
    SessionRate,
)


def _payment_status_for_booking(booking: SessionBooking) -> str:
    if int(booking.member_fee_amount_kobo or 0) == 0:
        return "included"
    if booking.payment_intent_id or booking.wallet_transaction_id:
        return "paid"
    return "unpaid"


async def ensure_session_participants(
    db: AsyncSession,
    session: Session,
) -> list[SessionParticipant]:
    """Materialize canonical participants from current booking/guest records."""
    now = utc_now()
    bookings = list(
        (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.session_id == session.id,
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
        ).scalars()
    )
    booking_ids = [booking.id for booking in bookings]
    booking_guests = (
        list(
            (
                await db.execute(
                    select(BookingGuest).where(BookingGuest.booking_id.in_(booking_ids))
                )
            ).scalars()
        )
        if booking_ids
        else []
    )
    guest_passes = list(
        (
            await db.execute(
                select(GuestPass).where(
                    GuestPass.session_id == session.id,
                    or_(
                        GuestPass.status.in_(["confirmed", "attended"]),
                        and_(
                            GuestPass.status.in_(["pending_payment", "payment_failed"]),
                            GuestPass.booking_mode == "reservation",
                            GuestPass.reservation_expires_at.is_not(None),
                            GuestPass.reservation_expires_at > now,
                        ),
                    ),
                )
            )
        ).scalars()
    )
    existing = list(
        (
            await db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.session_id == session.id
                )
            )
        ).scalars()
    )
    by_member = {
        str(row.member_id): row for row in existing if row.member_id is not None
    }
    by_booking_guest = {
        str(row.booking_guest_id): row
        for row in existing
        if row.booking_guest_id is not None
    }
    by_guest_pass = {
        str(row.guest_pass_id): row
        for row in existing
        if row.guest_pass_id is not None
    }

    rate_rows = list(
        (
            await db.execute(
                select(SessionRate).where(
                    SessionRate.session_id == session.id,
                    SessionRate.is_active.is_(True),
                )
            )
        ).scalars()
    )
    guest_rate = next(
        (
            rate
            for rate in sorted(rate_rows, key=lambda item: item.priority)
            if rate.audience == "guest" and rate.access_source is None
        ),
        None,
    )
    guest_fee = (
        int(guest_rate.amount_kobo)
        if guest_rate is not None
        else int(session.guest_fee_kobo or session.pool_fee or 0)
    )

    for booking in bookings:
        row = by_member.get(str(booking.member_id))
        if row is None:
            row = SessionParticipant(
                session_id=session.id,
                participant_kind="member",
                source="member_booking",
                member_id=booking.member_id,
                full_name_snapshot="Member",
            )
            db.add(row)
            await db.flush()
            by_member[str(booking.member_id)] = row
        row.booking_id = booking.id
        row.access_source = booking.access_source
        row.fee_amount_kobo = int(booking.member_fee_amount_kobo or 0)
        row.payment_status = _payment_status_for_booking(booking)
        row.waiver_status = "not_required"
        if row.source != "walk_in":
            row.source = "member_booking"

    by_booking_id = {booking.id: booking for booking in bookings}
    for guest in booking_guests:
        booking = by_booking_id.get(guest.booking_id)
        if booking is None:
            continue
        row = by_booking_guest.get(str(guest.id))
        if row is None:
            row = SessionParticipant(
                session_id=session.id,
                participant_kind="guest",
                source="attached_guest",
                booking_id=booking.id,
                booking_guest_id=guest.id,
                full_name_snapshot=guest.full_name or "Unnamed guest",
            )
            db.add(row)
            await db.flush()
            by_booking_guest[str(guest.id)] = row
        row.booking_id = booking.id
        row.full_name_snapshot = guest.full_name or row.full_name_snapshot
        row.phone_snapshot = guest.phone
        row.audience = "guest"
        row.rate_id = guest_rate.id if guest_rate else None
        row.rate_code = guest_rate.rate_code if guest_rate else "guest"
        row.fee_amount_kobo = guest_fee
        row.payment_status = (
            "paid"
            if booking.payment_intent_id or booking.wallet_transaction_id
            else ("included" if guest_fee == 0 else "unpaid")
        )
        row.waiver_status = "accepted" if guest.waiver_accepted_at else "unknown"

    for guest in guest_passes:
        row = by_guest_pass.get(str(guest.id))
        if row is None:
            row = SessionParticipant(
                session_id=session.id,
                participant_kind="guest",
                source="guest_pass",
                guest_pass_id=guest.id,
                full_name_snapshot=guest.full_name,
            )
            db.add(row)
            await db.flush()
            by_guest_pass[str(guest.id)] = row
        row.full_name_snapshot = guest.full_name
        row.email_snapshot = guest.email
        row.phone_snapshot = guest.phone
        row.audience = "guest"
        row.rate_id = guest_rate.id if guest_rate else None
        row.rate_code = guest_rate.rate_code if guest_rate else "guest"
        row.fee_amount_kobo = int(guest.price_kobo or 0)
        row.payment_status = (
            "paid" if guest.status in {"confirmed", "attended"} else "unpaid"
        )
        row.waiver_status = "accepted" if guest.waiver_accepted_at else "missing"

    await db.flush()
    return list(
        (
            await db.execute(
                select(SessionParticipant)
                .where(SessionParticipant.session_id == session.id)
                .order_by(SessionParticipant.created_at, SessionParticipant.id)
            )
        ).scalars()
    )


async def create_guest_walk_in(
    db: AsyncSession,
    *,
    session: Session,
    full_name: str,
    email: str | None,
    phone: str | None,
    fee_amount_kobo: int,
    payment_status: str,
    payment_method: str | None,
    payment_reference: str | None,
    waiver_status: str,
    notes: str | None,
    created_by: str,
) -> SessionParticipant:
    """Create a true unregistered walk-in without inventing a Member/GuestPass."""
    normalized_phone = phone.strip() if phone else None
    normalized_email = email.strip().lower() if email else None

    # A phone is the strongest available lightweight de-duplication key for an
    # unregistered swimmer. Fall back to email, then exact name for the same
    # session so repeated admin clicks do not manufacture duplicate people.
    query = select(SessionParticipant).where(
        SessionParticipant.session_id == session.id,
        SessionParticipant.source == "walk_in",
        SessionParticipant.participant_kind == "guest",
    )
    if normalized_phone:
        query = query.where(SessionParticipant.phone_snapshot == normalized_phone)
    elif normalized_email:
        query = query.where(SessionParticipant.email_snapshot == normalized_email)
    else:
        query = query.where(SessionParticipant.full_name_snapshot == full_name.strip())

    existing = (await db.execute(query)).scalar_one_or_none()
    if existing is not None:
        return existing

    guest_rate = (
        await db.execute(
            select(SessionRate)
            .where(
                SessionRate.session_id == session.id,
                SessionRate.audience == "guest",
                SessionRate.is_active.is_(True),
                SessionRate.access_source.is_(None),
            )
            .order_by(SessionRate.priority, SessionRate.id)
        )
    ).scalars().first()

    participant = SessionParticipant(
        session_id=session.id,
        participant_kind="guest",
        source="walk_in",
        full_name_snapshot=full_name.strip(),
        email_snapshot=normalized_email,
        phone_snapshot=normalized_phone,
        audience="guest",
        access_source="admin_walk_in",
        rate_id=guest_rate.id if guest_rate else None,
        rate_code=guest_rate.rate_code if guest_rate else "guest",
        fee_amount_kobo=max(0, int(fee_amount_kobo)),
        payment_status=payment_status,
        payment_method=payment_method,
        payment_reference=payment_reference,
        paid_at=utc_now() if payment_status == "paid" else None,
        waiver_status=waiver_status,
        notes=notes.strip() if notes else None,
        created_by=created_by,
    )
    db.add(participant)
    await db.flush()
    return participant
