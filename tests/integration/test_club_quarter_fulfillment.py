"""Real PostgreSQL checks for multi-series generation and prepaid attendance."""

from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from libs.auth.models import AuthUser
from services.sessions_service.models import (
    Session,
    SessionTemplate,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
)
from services.sessions_service.routers import (
    club_schedule,
    club_reservations,
    template_sync,
    bookings,
)
from services.sessions_service.services import club_generation, template_operations

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def template(club_id, pool_id, weekday=5):
    return SessionTemplate(
        id=uuid4(),
        title="Quarter practice",
        club_id=club_id,
        pool_id=pool_id,
        session_type=SessionType.CLUB,
        club_access_mode="plan_included",
        is_active=True,
        starts_on=date(2026, 1, 1),
        day_of_week=weekday,
        start_time=time(9),
        duration_minutes=120,
        capacity=20,
        pricing_settings={"pricing_expected_attendees": 20, "margin_value": 1200},
        admission_settings={
            "guest_fee": 10000,
            "guest_booking_mode": "public",
            "guest_booking_cutoff_hours": 2,
            "allows_community_dropins": True,
            "community_dropin_fee": 7000,
        },
    )


async def test_two_selected_series_inherit_full_defaults_and_retry(
    db_session, monkeypatch
):
    club, pool = uuid4(), uuid4()
    templates = [template(club, pool, 4), template(club, pool, 6)]
    db_session.add_all(templates)
    await db_session.commit()
    monkeypatch.setattr(
        club_generation,
        "fresh_club_price",
        AsyncMock(return_value={"pool_fee": 520000}),
    )
    monkeypatch.setattr(
        template_operations,
        "get_partner_pool",
        AsyncMock(return_value={"name": "Rowe Park Pool", "address": "Yaba"}),
    )
    operations = AsyncMock()
    monkeypatch.setattr(club_schedule, "materialise_template_operations", operations)
    body = club_schedule.GenerateQuarter(
        club_id=club,
        pool_id=pool,
        template_ids=[t.id for t in templates],
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        weekday=5,
        starts_at_local=time(9),
        duration_minutes=60,
    )
    first = await club_schedule.generate_quarter(body, db_session)
    second = await club_schedule.generate_quarter(body, db_session)
    assert len(first) == 26 and {s["id"] for s in first} == {s["id"] for s in second}
    rows = (
        (await db_session.execute(select(Session).where(Session.club_id == club)))
        .scalars()
        .all()
    )
    assert len(rows) == 26
    for row in rows:
        assert row.location_name == "Rowe Park Pool" and row.location_address == "Yaba"
        assert row.guest_fee_kobo == 1000000 and row.community_dropin_fee_kobo == 700000
        assert (
            row.allows_guests
            and row.allows_community_dropins
            and row.guest_booking_mode == "public"
        )
        assert row.guest_booking_closes_at == row.starts_at - timedelta(hours=2)
        assert row.pool_fee == 520000 and row.status == SessionStatus.DRAFT
    assert operations.await_count == 52  # Replays also repair interrupted fan-out.

    # The same exact quarter generated from both templates is what checkout
    # promises. Every future inclusion must hold a seat, across both series.
    from services.sessions_service.routers import club_holds
    from services.sessions_service.services.club_holds import held_club_seats

    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(club_holds, "utc_now", lambda: now)
    monkeypatch.setattr(
        "services.sessions_service.services.club_holds.utc_now", lambda: now
    )
    for row in rows:
        row.status = SessionStatus.SCHEDULED
        row.published_at = now
    await db_session.commit()
    purchase = club_holds.ReserveClubHolds(
        application_id=uuid4(),
        club_id=club,
        member_id=uuid4(),
        payment_reference=f"multi-template-{uuid4()}",
        expires_at=now + timedelta(minutes=30),
        plans=[
            club_holds.HoldPlan(
                plan_version_id=uuid4(),
                starts_at=now,
                ends_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
                session_ids=[row.id for row in rows],
            )
        ],
    )
    held = await club_holds.reserve_holds(purchase, db_session)
    assert len(held["session_ids"]) == 26
    for row in rows:
        assert await held_club_seats(db_session, row.id) == 1


async def test_bulk_repair_requires_current_preview_and_keeps_sold_terms(
    db_session, monkeypatch
):
    club, pool = uuid4(), uuid4()
    parent = template(club, pool)
    db_session.add(parent)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    starts = now + timedelta(days=5)
    row = Session(
        title="Sold swim",
        session_type=SessionType.CLUB,
        club_id=club,
        pool_id=pool,
        template_id=parent.id,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=SessionStatus.SCHEDULED,
        pool_fee=523456,
        capacity=19,
        notes="Keep this",
    )
    db_session.add(row)
    await db_session.commit()
    monkeypatch.setattr(
        template_sync,
        "template_location",
        AsyncMock(return_value={"location_name": "Rowe", "location_address": "Yaba"}),
    )
    monkeypatch.setattr(
        template_sync, "template_volunteer_slots", AsyncMock(return_value=[])
    )
    ops = AsyncMock()
    monkeypatch.setattr(template_sync, "materialise_template_operations", ops)
    body = template_sync.TemplateSyncRequest(
        from_date=now.date(), to_date=starts.date() + timedelta(days=1)
    )
    preview = await template_sync.sync_template_operations(parent.id, body, db_session)
    assert not preview["applied"] and row.location_name is None
    with pytest.raises(HTTPException) as error:
        await template_sync.sync_template_operations(
            parent.id, body.model_copy(update={"preview_token": "stale"}), db_session
        )
    assert error.value.status_code == 409
    result = await template_sync.sync_template_operations(
        parent.id,
        body.model_copy(update={"preview_token": preview["preview_token"]}),
        db_session,
    )
    assert result["sessions_updated"] == 1 and not result["warnings"]
    assert (row.pool_fee, row.capacity, row.starts_at, row.notes) == (
        523456,
        19,
        starts,
        "Keep this",
    )
    assert row.guest_fee_kobo == 1000000 and row.location_name == "Rowe"
    ops.assert_awaited_once()


def reservation_body(club, member, sessions):
    now = datetime.now(timezone.utc)
    return club_reservations.PrepaidReservationsRequest(
        enrollment_id=uuid4(),
        club_id=club,
        member_id=member,
        member_auth_id=str(member),
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=90),
        session_ids=[s.id for s in sessions],
    )


async def swims(db, club, offsets=(1, 2, -2), capacity=20):
    now = datetime.now(timezone.utc)
    rows = [
        Session(
            id=uuid4(),
            title="Included",
            session_type=SessionType.CLUB,
            club_id=club,
            pool_id=uuid4(),
            starts_at=now + timedelta(days=d),
            ends_at=now + timedelta(days=d, hours=1),
            status=SessionStatus.SCHEDULED,
            pool_fee=520000,
            capacity=capacity,
        )
        for d in offsets
    ]
    db.add_all(rows)
    await db.commit()
    return rows


async def test_prepaid_future_only_retry_and_cancellation_never_refunds(
    db_session, monkeypatch
):
    club, member = uuid4(), uuid4()
    rows = await swims(db_session, club)
    sync = AsyncMock()
    monkeypatch.setattr(club_reservations, "sync_booking_attendance", sync)
    monkeypatch.setattr(bookings, "sync_booking_attendance", sync)
    credit = AsyncMock()
    monkeypatch.setattr(bookings, "credit_member_wallet", credit)
    body = reservation_body(club, member, rows)
    assert (await club_reservations.reserve_prepaid_swims(body, db_session))[
        "created"
    ] == 2
    assert (await club_reservations.reserve_prepaid_swims(body, db_session))[
        "created"
    ] == 0
    saved = (
        (
            await db_session.execute(
                select(SessionBooking).where(SessionBooking.member_id == member)
            )
        )
        .scalars()
        .all()
    )
    assert len(saved) == 2 and all(
        b.fee_amount_kobo == 0
        and b.member_fee_amount_kobo == 0
        and b.access_source == "quarterly_prepaid"
        and b.booking_source == "club_quarter"
        for b in saved
    )
    await bookings.cancel_booking(saved[0].id, AuthUser(sub=str(member)), db_session)
    await club_reservations.reserve_prepaid_swims(body, db_session)
    assert saved[0].status == SessionBookingStatus.CANCELLED
    credit.assert_not_awaited()


async def test_prepaid_capacity_counts_existing_guests_and_is_atomic(
    db_session, monkeypatch
):
    club, member = uuid4(), uuid4()
    rows = await swims(db_session, club, (1, 2), capacity=2)
    # Fill the last session in the same lock order, to exercise rollback after an insert.
    last = max(rows, key=lambda row: row.id)
    db_session.add(
        SessionBooking(
            session_id=last.id,
            member_id=uuid4(),
            member_auth_id=str(uuid4()),
            party_size=2,
            status=SessionBookingStatus.CONFIRMED,
            fee_amount_kobo=100,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(club_reservations, "sync_booking_attendance", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await club_reservations.reserve_prepaid_swims(
            reservation_body(club, member, rows), db_session
        )
    assert error.value.status_code == 409
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(func.count(SessionBooking.id)).where(
                SessionBooking.member_id == member
            )
        )
        == 0
    )


async def test_paid_fulfillment_retry_repairs_attendance_without_duplicate_bookings(
    db_session, monkeypatch
):
    club, member = uuid4(), uuid4()
    rows = await swims(db_session, club, (1, 2))
    sync = AsyncMock(side_effect=HTTPException(502, "attendance temporarily down"))
    monkeypatch.setattr(club_reservations, "sync_booking_attendance", sync)
    body = reservation_body(club, member, rows)
    with pytest.raises(HTTPException):
        await club_reservations.reserve_prepaid_swims(body, db_session)
    sync.side_effect = None
    result = await club_reservations.reserve_prepaid_swims(body, db_session)
    assert result == {"created": 0, "confirmed": 2}
    assert (
        await db_session.scalar(
            select(func.count(SessionBooking.id)).where(
                SessionBooking.member_id == member
            )
        )
        == 2
    )
    assert sync.await_count == 4


async def test_settlement_view_is_scoped_to_booking_owner(db_session):
    from services.sessions_service.routers.member_booking_details import (
        my_booking_settlement,
    )

    club, member = uuid4(), uuid4()
    row = (await swims(db_session, club, (1,)))[0]
    booking = SessionBooking(
        session_id=row.id,
        member_id=member,
        member_auth_id=str(member),
        status=SessionBookingStatus.CONFIRMED,
        fee_amount_kobo=520000,
    )
    db_session.add(booking)
    await db_session.commit()
    own = await my_booking_settlement(booking.id, AuthUser(sub=str(member)), db_session)
    assert own["fee_amount_kobo"] == 520000 and not own["settled"]
    with pytest.raises(HTTPException) as error:
        await my_booking_settlement(booking.id, AuthUser(sub=str(uuid4())), db_session)
    assert error.value.status_code == 404
