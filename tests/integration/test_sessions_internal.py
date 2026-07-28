"""Integration tests for sessions_service internal endpoints."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from libs.common.session_access import SessionAccessDecision
from tests.factories import MemberFactory, SessionCoachFactory, SessionFactory


@pytest.fixture(autouse=True)
def _stub_booking_attendance_sync():
    with patch(
        "services.sessions_service.routers.internal.sync_booking_attendance",
        AsyncMock(return_value=None),
    ):
        yield


# ---------------------------------------------------------------------------
# GET /internal/sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_by_id(sessions_client, db_session):
    """Internal session lookup returns operational email and capacity data."""
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    pool_id = uuid.uuid4()
    member_id = uuid.uuid4()
    coach_id = uuid.uuid4()
    session = SessionFactory.create(
        pool_id=pool_id,
        description="Technique and pacing",
    )
    booking = SessionBooking(
        session_id=session.id,
        member_id=member_id,
        member_auth_id="auth-member",
        status=SessionBookingStatus.CONFIRMED,
        channel=BookingChannel.MEMBER_SELF,
        party_size=2,
        fee_amount_kobo=4000,
    )
    coach = SessionCoachFactory.create(
        session_id=session.id,
        coach_id=coach_id,
    )
    db_session.add_all([session, booking, coach])
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["title"] == session.title
    assert data["session_type"] == "club"
    assert data["status"] == "scheduled"
    assert data["capacity"] == 20
    assert data["pool_id"] == str(pool_id)
    assert data["description"] == "Technique and pacing"
    assert data["occupied_slots"] == 2
    assert data["confirmed_booking_member_ids"] == [str(member_id)]
    assert data["coach_member_ids"] == [str(coach_id)]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_by_id_not_found(sessions_client):
    """Returns 404 for non-existent session."""
    import uuid

    response = await sessions_client.get(f"/internal/sessions/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_summaries_batch_preserves_requested_order(
    sessions_client, db_session
):
    first = SessionFactory.create(title="First summary")
    second = SessionFactory.create(title="Second summary")
    missing_id = uuid.uuid4()
    db_session.add_all([first, second])
    await db_session.commit()

    response = await sessions_client.post(
        "/internal/sessions/summaries/batch",
        json={
            "session_ids": [
                str(second.id),
                str(missing_id),
                str(first.id),
                str(second.id),
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [
        str(second.id),
        str(first.id),
    ]
    assert [item["title"] for item in response.json()] == [
        "Second summary",
        "First summary",
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_campaign_booking_stats_are_backend_attributed(
    sessions_client, db_session
):
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    campaign = "week-2026-07-20"
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.flush()
    db_session.add_all(
        [
            SessionBooking(
                session_id=session.id,
                member_id=uuid.uuid4(),
                member_auth_id=str(uuid.uuid4()),
                status=SessionBookingStatus.PENDING,
                channel=BookingChannel.MEMBER_SELF,
                campaign_key=campaign,
            ),
            SessionBooking(
                session_id=session.id,
                member_id=uuid.uuid4(),
                member_auth_id=str(uuid.uuid4()),
                status=SessionBookingStatus.CONFIRMED,
                channel=BookingChannel.MEMBER_SELF,
                campaign_key=campaign,
            ),
        ]
    )
    await db_session.commit()

    response = await sessions_client.get(
        "/internal/sessions/bookings/campaign-stats",
        params={"campaign_key": campaign},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "campaign_key": campaign,
        "total": 2,
        "pending": 1,
        "confirmed": 1,
        "cancelled": 0,
        "expired": 0,
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_access_preserves_confirmed_booking_but_enforces_window(
    sessions_client, db_session, monkeypatch
):
    import services.sessions_service.routers.internal as internal_router
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    session = SessionFactory.create(
        starts_at=now - timedelta(minutes=15),
        ends_at=now + timedelta(minutes=45),
    )
    booking = SessionBooking(
        session_id=session.id,
        member_id=member_id,
        member_auth_id=auth_id,
        status=SessionBookingStatus.CONFIRMED,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=0,
        confirmed_at=now - timedelta(days=1),
    )
    db_session.add_all([session, booking])
    await db_session.commit()

    async def fake_member_lookup(*args, **kwargs):
        return {"id": str(member_id), "auth_id": auth_id}

    async def unexpected_membership_lookup(**kwargs):
        raise AssertionError("confirmed bookings must not re-check membership")

    monkeypatch.setattr(internal_router, "get_member_by_auth_id", fake_member_lookup)
    monkeypatch.setattr(
        internal_router,
        "get_member_session_access_payload",
        unexpected_membership_lookup,
    )

    response = await sessions_client.get(
        f"/internal/sessions/{session.id}/access",
        params={"member_auth_id": auth_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["confirmed_booking"] is True
    assert response.json()["confirmed_booking_id"] == str(booking.id)
    assert response.json()["sign_in_allowed"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_access_rejects_confirmed_booking_outside_window(
    sessions_client, db_session, monkeypatch
):
    import services.sessions_service.routers.internal as internal_router
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    session = SessionFactory.create(
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=3),
    )
    booking = SessionBooking(
        session_id=session.id,
        member_id=member_id,
        member_auth_id=auth_id,
        status=SessionBookingStatus.CONFIRMED,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=0,
        confirmed_at=now,
    )
    db_session.add_all([session, booking])
    await db_session.commit()

    async def fake_member_lookup(*args, **kwargs):
        return {"id": str(member_id), "auth_id": auth_id}

    monkeypatch.setattr(internal_router, "get_member_by_auth_id", fake_member_lookup)

    response = await sessions_client.get(
        f"/internal/sessions/{session.id}/access",
        params={"member_auth_id": auth_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["confirmed_booking"] is True
    assert response.json()["sign_in_allowed"] is False
    assert response.json()["reason"] == "session_unavailable"


# ---------------------------------------------------------------------------
# GET /internal/sessions/cohorts/{cohort_id}/next-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_next_session_for_cohort(sessions_client, db_session):
    """Returns the next upcoming SCHEDULED session for a cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    # Past session (should be skipped)
    past = SessionFactory.create(
        cohort_id=cohort_id,
        starts_at=datetime.now(timezone.utc) - timedelta(days=1),
        ends_at=datetime.now(timezone.utc) - timedelta(hours=22),
    )
    # Next upcoming session
    upcoming = SessionFactory.create(
        cohort_id=cohort_id,
        title="Next Lesson",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, hours=2),
    )
    # Further future session
    future = SessionFactory.create(
        cohort_id=cohort_id,
        starts_at=datetime.now(timezone.utc) + timedelta(days=7),
        ends_at=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
    )
    db_session.add_all([past, upcoming, future])
    await db_session.commit()

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/next-session"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Next Lesson"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_next_session_for_cohort_not_found(sessions_client):
    """Returns 404 when no upcoming sessions exist for the cohort."""
    import uuid

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{uuid.uuid4()}/next-session"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /internal/sessions/bookings/{booking_id}/confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paid_booking_confirmation_restores_an_expired_reservation(
    sessions_client, db_session
):
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    member_id = uuid.uuid4()
    member_auth_id = str(uuid.uuid4())
    payment_intent_id = uuid.uuid4()
    session = SessionFactory.create(capacity=1)
    booking = SessionBooking(
        session_id=session.id,
        member_id=member_id,
        member_auth_id=member_auth_id,
        status=SessionBookingStatus.EXPIRED,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=200000,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add_all([session, booking])
    await db_session.commit()

    response = await sessions_client.post(
        f"/internal/sessions/bookings/{booking.id}/confirm",
        json={
            "member_auth_id": member_auth_id,
            "payment_intent_id": str(payment_intent_id),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"
    await db_session.refresh(booking)
    assert booking.status == SessionBookingStatus.CONFIRMED
    assert booking.payment_intent_id == payment_intent_id
    assert booking.expires_at is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paid_booking_confirmation_rejects_a_different_member(
    sessions_client, db_session
):
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
    )

    session = SessionFactory.create()
    booking = SessionBooking(
        session_id=session.id,
        member_id=uuid.uuid4(),
        member_auth_id=str(uuid.uuid4()),
        status=SessionBookingStatus.PENDING,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=200000,
    )
    db_session.add_all([session, booking])
    await db_session.commit()

    response = await sessions_client.post(
        f"/internal/sessions/bookings/{booking.id}/confirm",
        json={
            "member_auth_id": str(uuid.uuid4()),
            "payment_intent_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 409, response.text
    await db_session.refresh(booking)
    assert booking.status == SessionBookingStatus.PENDING
    assert booking.payment_intent_id is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paid_booking_confirmation_rejects_a_cancelled_session(
    sessions_client, db_session
):
    from services.sessions_service.models import (
        BookingChannel,
        SessionBooking,
        SessionBookingStatus,
        SessionStatus,
    )

    member_auth_id = str(uuid.uuid4())
    session = SessionFactory.create(status=SessionStatus.CANCELLED)
    booking = SessionBooking(
        session_id=session.id,
        member_id=uuid.uuid4(),
        member_auth_id=member_auth_id,
        status=SessionBookingStatus.PENDING,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=200000,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db_session.add_all([session, booking])
    await db_session.commit()

    response = await sessions_client.post(
        f"/internal/sessions/bookings/{booking.id}/confirm",
        json={
            "member_auth_id": member_auth_id,
            "payment_intent_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 409, response.text
    await db_session.refresh(booking)
    assert booking.status == SessionBookingStatus.PENDING


# ---------------------------------------------------------------------------
# POST /internal/sessions/bookings/bundle/reserve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bundle_reservation_uses_database_prices_and_creates_pending_rows(
    sessions_client, db_session, monkeypatch
):
    from sqlalchemy import select

    import services.sessions_service.routers.internal as internal_router
    from services.sessions_service.models import SessionBooking, SessionBookingStatus

    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())
    payment_id = uuid.uuid4()
    first = SessionFactory.create(pool_fee=125000)
    second = SessionFactory.create(pool_fee=225000)
    db_session.add_all([first, second])
    await db_session.commit()

    async def fake_member_lookup(*args, **kwargs):
        return {"id": str(member_id), "auth_id": auth_id}

    async def fake_member_payload(**kwargs):
        return {"id": str(member_id), "member_id": str(member_id)}

    async def fake_access(**kwargs):
        return SessionAccessDecision(
            required_tier="club",
            visible=True,
            bookable=True,
            digest_eligible=True,
            prompt_eligible=True,
            sign_in_allowed=True,
        )

    monkeypatch.setattr(internal_router, "get_member_by_auth_id", fake_member_lookup)
    monkeypatch.setattr(
        internal_router, "get_member_session_access_payload", fake_member_payload
    )
    monkeypatch.setattr(
        internal_router, "evaluate_session_access_for_member", fake_access
    )

    response = await sessions_client.post(
        "/internal/sessions/bookings/bundle/reserve",
        json={
            "member_auth_id": auth_id,
            "payment_intent_id": str(payment_id),
            "session_ids": [str(first.id), str(second.id)],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pool_total_kobo"] == 350000
    assert [line["amount_kobo"] for line in body["lines"]] == [125000, 225000]

    bookings = (
        (
            await db_session.execute(
                select(SessionBooking).where(
                    SessionBooking.payment_intent_id == payment_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(bookings) == 2
    assert all(row.status == SessionBookingStatus.PENDING for row in bookings)
    assert all(row.member_id == member_id for row in bookings)

    confirm = await sessions_client.post(
        "/internal/sessions/bookings/bundle/confirm",
        json={
            "member_auth_id": auth_id,
            "payment_intent_id": str(payment_id),
            "booking_ids": [line["booking_id"] for line in body["lines"]],
        },
    )

    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["confirmed"] == 2
    for booking in bookings:
        await db_session.refresh(booking)
    assert all(row.status == SessionBookingStatus.CONFIRMED for row in bookings)
    assert all(row.expires_at is None for row in bookings)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bundle_reservation_rejects_second_active_payment(
    sessions_client, db_session, monkeypatch
):
    import services.sessions_service.routers.internal as internal_router

    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())
    session = SessionFactory.create(pool_fee=100000)
    db_session.add(session)
    await db_session.commit()

    async def fake_member_lookup(*args, **kwargs):
        return {"id": str(member_id), "auth_id": auth_id}

    async def fake_member_payload(**kwargs):
        return {"id": str(member_id), "member_id": str(member_id)}

    async def fake_access(**kwargs):
        return SessionAccessDecision(
            required_tier="club",
            visible=True,
            bookable=True,
            digest_eligible=True,
            prompt_eligible=True,
            sign_in_allowed=True,
        )

    monkeypatch.setattr(internal_router, "get_member_by_auth_id", fake_member_lookup)
    monkeypatch.setattr(
        internal_router, "get_member_session_access_payload", fake_member_payload
    )
    monkeypatch.setattr(
        internal_router, "evaluate_session_access_for_member", fake_access
    )

    first = await sessions_client.post(
        "/internal/sessions/bookings/bundle/reserve",
        json={
            "member_auth_id": auth_id,
            "payment_intent_id": str(uuid.uuid4()),
            "session_ids": [str(session.id)],
        },
    )
    second = await sessions_client.post(
        "/internal/sessions/bookings/bundle/reserve",
        json={
            "member_auth_id": auth_id,
            "payment_intent_id": str(uuid.uuid4()),
            "session_ids": [str(session.id)],
        },
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "payment is already in progress" in second.json()["detail"]


# ---------------------------------------------------------------------------
# GET /internal/sessions/cohorts/{cohort_id}/session-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_ids_for_cohort(sessions_client, db_session):
    """Returns all session IDs for a given cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    s1 = SessionFactory.create(cohort_id=cohort_id)
    s2 = SessionFactory.create(cohort_id=cohort_id)
    # Different cohort — should not appear
    s3 = SessionFactory.create(cohort_id=uuid.uuid4())
    db_session.add_all([s1, s2, s3])
    await db_session.commit()

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/session-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(s1.id) in data
    assert str(s2.id) in data
    assert str(s3.id) not in data


# ---------------------------------------------------------------------------
# GET /internal/sessions/cohorts/{cohort_id}/completed-session-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_completed_session_ids_for_cohort(sessions_client, db_session):
    """Returns only completed session IDs for a cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    completed = SessionFactory.create(cohort_id=cohort_id, status="COMPLETED")
    scheduled = SessionFactory.create(cohort_id=cohort_id, status="SCHEDULED")
    db_session.add_all([completed, scheduled])
    await db_session.commit()

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/completed-session-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(completed.id) in data
    assert str(scheduled.id) not in data


# ---------------------------------------------------------------------------
# GET /internal/sessions/{session_id}/coaches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_coach_ids(sessions_client, db_session):
    """Returns coach member IDs assigned to a session."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.flush()

    coach_assignment = SessionCoachFactory.create(
        session_id=session.id,
        coach_id=member.id,
    )
    db_session.add(coach_assignment)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}/coaches")

    assert response.status_code == 200
    data = response.json()
    assert str(member.id) in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_coach_ids_empty(sessions_client, db_session):
    """Returns empty list when no coaches assigned."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}/coaches")

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /internal/sessions/scheduled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_scheduled_sessions(sessions_client, db_session):
    """Returns only SCHEDULED sessions."""
    scheduled = SessionFactory.create(status="SCHEDULED", title="Active")
    cancelled = SessionFactory.create(status="CANCELLED", title="Cancelled")
    db_session.add_all([scheduled, cancelled])
    await db_session.commit()

    response = await sessions_client.get("/internal/sessions/scheduled")

    assert response.status_code == 200
    data = response.json()
    titles = [s["title"] for s in data]
    assert "Active" in titles
    assert "Cancelled" not in titles


# ---------------------------------------------------------------------------
# Regression: date-range filters on the scheduled / completed-ids endpoints.
# start_date/end_date were typed Optional[str] and compared against the
# timestamptz `starts_at`, raising psycopg UndefinedFunction (500). The bare
# (no-date) tests above never exercised this path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_scheduled_sessions_date_range(sessions_client, db_session):
    """Scheduled endpoint filters by start_date/end_date without 500ing."""
    now = datetime.now(timezone.utc)
    in_window = SessionFactory.create(
        status="SCHEDULED",
        title="InWindow",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2) + timedelta(hours=2),
    )
    after_window = SessionFactory.create(
        status="SCHEDULED",
        title="AfterWindow",
        starts_at=now + timedelta(days=10),
        ends_at=now + timedelta(days=10) + timedelta(hours=2),
    )
    db_session.add_all([in_window, after_window])
    await db_session.commit()

    response = await sessions_client.get(
        "/internal/sessions/scheduled",
        params={
            "start_date": (now + timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=5)).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    titles = [s["title"] for s in response.json()]
    assert "InWindow" in titles
    assert "AfterWindow" not in titles


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_completed_session_ids_date_range(sessions_client, db_session):
    """Completed-ids endpoint filters by start_date/end_date without 500ing."""
    import uuid

    cohort_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    in_window = SessionFactory.create(
        cohort_id=cohort_id,
        status="COMPLETED",
        starts_at=now - timedelta(days=3),
        ends_at=now - timedelta(days=3) + timedelta(hours=2),
    )
    out_window = SessionFactory.create(
        cohort_id=cohort_id,
        status="COMPLETED",
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(days=30) + timedelta(hours=2),
    )
    db_session.add_all([in_window, out_window])
    await db_session.commit()

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/completed-session-ids",
        params={
            "start_date": (now - timedelta(days=7)).isoformat(),
            "end_date": now.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert str(in_window.id) in data
    assert str(out_window.id) not in data
