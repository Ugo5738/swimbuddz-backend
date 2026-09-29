"""Seat guarantees use real PostgreSQL locks, including independent buyers."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    ClubSessionHold,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
)
from services.sessions_service.routers import club_holds, club_reservations
from services.sessions_service.routers.guest_passes import _spaces_remaining
from services.sessions_service.services.booking_capacity import assert_booking_capacity
from services.sessions_service.services.club_holds import held_club_seats

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def swims(capacities=(2, 1)):
    club_id = uuid4()
    return [
        Session(
            id=uuid4(),
            club_id=club_id,
            title="Included swim",
            session_type=SessionType.CLUB,
            club_access_mode="plan_included",
            status=SessionStatus.SCHEDULED,
            capacity=capacity,
            starts_at=utc_now() + timedelta(days=offset + 5),
            ends_at=utc_now() + timedelta(days=offset + 5, hours=1),
        )
        for offset, capacity in enumerate(capacities)
    ]


def checkout(rows, *, member_id=None):
    return club_holds.ReserveClubHolds(
        application_id=uuid4(),
        club_id=rows[0].club_id,
        member_id=member_id or uuid4(),
        payment_reference=f"club-test-{uuid4()}",
        expires_at=utc_now() + timedelta(minutes=30),
        plans=[
            club_holds.HoldPlan(
                plan_version_id=uuid4(),
                starts_at=utc_now(),
                ends_at=utc_now() + timedelta(days=90),
                session_ids=[row.id for row in rows],
            )
        ],
    )


def action(body):
    return club_holds.HoldAction(
        application_id=body.application_id, payment_reference=body.payment_reference
    )


async def test_all_future_swims_held_and_guests_dropins_cannot_take_them(db_session):
    rows = swims((1, 1))
    historical = swims((1,))[0]
    historical.club_id = rows[0].club_id
    historical.starts_at, historical.ends_at = (
        utc_now() - timedelta(days=3),
        utc_now() - timedelta(days=3, hours=-1),
    )
    db_session.add_all([*rows, historical])
    await db_session.commit()
    body = checkout([*rows, historical])
    first = await club_holds.reserve_holds(body, db_session)
    second = await club_holds.reserve_holds(body, db_session)
    assert (
        set(first["session_ids"])
        == set(second["session_ids"])
        == {str(row.id) for row in rows}
    )
    for row in rows:
        assert await _spaces_remaining(row, db_session) == 0
        with pytest.raises(HTTPException, match="full"):
            await assert_booking_capacity(
                db_session, session=row, member_id=uuid4(), new_party_size=1
            )
    assert await held_club_seats(db_session, historical.id) == 0


async def test_lower_capacity_swim_rejects_whole_quarter_before_payment(db_session):
    rows = swims()
    db_session.add_all(rows)
    db_session.add(
        SessionBooking(
            session_id=rows[1].id,
            member_id=uuid4(),
            member_auth_id="other",
            status=SessionBookingStatus.CONFIRMED,
            fee_amount_kobo=0,
        )
    )
    await db_session.commit()
    body = checkout(rows)
    with pytest.raises(HTTPException, match="full"):
        await club_holds.reserve_holds(body, db_session)
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ClubSessionHold)
            .where(ClubSessionHold.payment_reference == body.payment_reference)
        )
        == 0
    )


async def test_unused_expiry_release_and_exposed_payment_protection(
    db_session, monkeypatch
):
    rows = swims((1,))
    db_session.add_all(rows)
    await db_session.commit()
    body = checkout(rows)
    await club_holds.reserve_holds(body, db_session)
    later = utc_now() + timedelta(hours=2)
    monkeypatch.setattr(
        "services.sessions_service.services.club_holds.utc_now", lambda: later
    )
    assert await held_club_seats(db_session, rows[0].id) == 0
    await club_holds.release_holds(action(body), db_session)
    await club_holds.reserve_holds(body, db_session)
    await club_holds.protect_holds(action(body), db_session)
    assert await held_club_seats(db_session, rows[0].id) == 1
    with pytest.raises(HTTPException, match="reconciliation"):
        await club_holds.release_holds(action(body), db_session)
    await db_session.rollback()
    assert await held_club_seats(db_session, body.plans[0].session_ids[0]) == 1
    verified = action(body).model_copy(
        update={
            "closure_evidence": "Provider support verified checkout disabled CASE-1234"
        }
    )
    await club_holds.release_holds(verified, db_session)
    await club_holds.release_holds(verified, db_session)
    assert await held_club_seats(db_session, body.plans[0].session_ids[0]) == 0


async def test_paid_conversion_replay_and_cancellation_stays_cancelled(
    db_session, monkeypatch
):
    rows = swims((1, 1))
    db_session.add_all(rows)
    await db_session.commit()
    body = checkout(rows)
    await club_holds.reserve_holds(body, db_session)
    await club_holds.protect_holds(action(body), db_session)
    sync = AsyncMock(side_effect=HTTPException(502, "Attendance unavailable"))
    monkeypatch.setattr(club_reservations, "sync_booking_attendance", sync)
    purchased = club_reservations.PrepaidReservationsRequest(
        enrollment_id=uuid4(),
        club_id=body.club_id,
        member_id=body.member_id,
        member_auth_id=str(body.member_id),
        starts_at=utc_now(),
        ends_at=utc_now() + timedelta(days=90),
        session_ids=[row.id for row in rows],
        payment_reference=body.payment_reference,
        require_holds=True,
    )
    with pytest.raises(HTTPException, match="Attendance"):
        await club_reservations.reserve_prepaid_swims(purchased, db_session)
    sync.side_effect = None
    assert (await club_reservations.reserve_prepaid_swims(purchased, db_session))[
        "created"
    ] == 0
    bookings = list(
        (
            await db_session.execute(
                select(SessionBooking).where(SessionBooking.member_id == body.member_id)
            )
        ).scalars()
    )
    assert len(bookings) == 2 and all(
        booking.fee_amount_kobo == 0 for booking in bookings
    )
    bookings[0].status = SessionBookingStatus.CANCELLED
    await db_session.commit()
    assert (await club_reservations.reserve_prepaid_swims(purchased, db_session))[
        "confirmed"
    ] == 1
    assert bookings[0].status == SessionBookingStatus.CANCELLED
    holds = list(
        (
            await db_session.execute(
                select(ClubSessionHold).where(
                    ClubSessionHold.payment_reference == body.payment_reference
                )
            )
        ).scalars()
    )
    assert all(hold.status == "consumed" for hold in holds)


async def test_competing_buyers_final_seat_uses_independent_transactions(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    rows = swims((1, 1))
    ids = [row.id for row in rows]
    async with factory() as db:
        db.add_all(rows)
        await db.commit()

    async def purchase(body):
        async with factory() as db:
            try:
                return await club_holds.reserve_holds(body, db)
            except HTTPException as error:
                await db.rollback()
                return error.status_code

    try:
        left, right = checkout(rows), checkout(list(reversed(rows)))
        results = await asyncio.wait_for(
            asyncio.gather(purchase(left), purchase(right)), 10
        )
        assert (
            sum(isinstance(result, dict) for result in results) == 1 and 409 in results
        )
        async with factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ClubSessionHold)
                    .where(ClubSessionHold.session_id.in_(ids))
                )
                == 2
            )
    finally:
        async with factory() as db:
            await db.execute(
                delete(ClubSessionHold).where(ClubSessionHold.session_id.in_(ids))
            )
            await db.execute(delete(Session).where(Session.id.in_(ids)))
            await db.commit()
