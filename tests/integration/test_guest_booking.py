"""Integration tests for guest/group booking (slices 1b + 2).

Exercises the DB-bound paths the unit tests can't reach: guest persistence,
server-computed fee, head-count capacity, and the guest check-in. Runs against
the dev DB via the transactional-rollback ``db_session`` fixture. Service-client
calls are patched on the *router module* (the functions are imported there with
``from ... import fn``, so patching libs.common.service_client wouldn't bind).
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

BOOKINGS = "services.sessions_service.routers.bookings"
SESSION_ACCESS = "services.sessions_service.services.session_access"
SIGN_IN = "services.attendance_service.routers.member.sign_in"


@pytest.fixture(autouse=True)
def _stub_booking_attendance_sync():
    with patch(f"{BOOKINGS}.sync_booking_attendance", AsyncMock(return_value=None)):
        yield


def _start():
    return datetime(2030, 6, 1, 9, 0, tzinfo=timezone.utc)


async def _make_community_session(
    db_session, *, pool_fee=3500, capacity=20, allows_guests=True, max_guests=4
):
    from services.sessions_service.models import Session
    from services.sessions_service.models.enums import SessionStatus, SessionType

    s = Session(
        session_type=SessionType.COMMUNITY,
        status=SessionStatus.SCHEDULED,
        title="Guest Test Meet",
        starts_at=_start(),
        ends_at=_start() + timedelta(hours=2),
        capacity=capacity,
        pool_fee=pool_fee,
        allows_guests=allows_guests,
        max_guests_per_booking=max_guests,
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    return s


async def _make_club_session(
    db_session,
    *,
    pool_fee=900_000,
    guest_fee=700_000,
    community_dropin_fee=650_000,
    capacity=20,
):
    from services.sessions_service.models import Session
    from services.sessions_service.models.enums import SessionStatus, SessionType

    session = Session(
        session_type=SessionType.CLUB,
        status=SessionStatus.SCHEDULED,
        title="Saturday Club Pricing Test",
        starts_at=_start(),
        ends_at=_start() + timedelta(hours=2),
        capacity=capacity,
        pool_fee=pool_fee,
        guest_fee_kobo=guest_fee,
        community_dropin_fee_kobo=community_dropin_fee,
        allows_community_dropins=True,
        allows_guests=True,
        max_guests_per_booking=4,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


def _patch_member_wallet():
    """Patch the member-lookup + wallet-debit the book endpoint imports."""
    member_id = uuid.uuid4()
    return (
        patch(
            f"{BOOKINGS}.get_member_by_auth_id",
            AsyncMock(return_value={"id": str(member_id), "auth_id": "auth-x"}),
        ),
        patch(
            f"{SESSION_ACCESS}.get_member_membership",
            AsyncMock(
                return_value={
                    "member_id": str(member_id),
                    "primary_tier": "community",
                    "active_tiers": ["community"],
                    "community_paid_until": "2035-01-01T00:00:00+00:00",
                    "club_paid_until": None,
                    "academy_paid_until": None,
                }
            ),
        ),
        patch(
            f"{BOOKINGS}.debit_member_wallet",
            AsyncMock(return_value={"transaction_id": str(uuid.uuid4())}),
        ),
    )


@pytest.mark.asyncio
async def test_book_with_guests_persists_and_computes_fee(sessions_client, db_session):
    from services.sessions_service.models import BookingGuest

    session = await _make_community_session(db_session, pool_fee=350_000)
    p_member, p_membership, p_wallet = _patch_member_wallet()
    with p_member, p_membership, p_wallet:
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "pay_with_bubbles": True,
                "guests": [
                    {"full_name": "Ada Friend", "phone": "0803"},
                    {"full_name": "Bee Friend"},
                ],
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["party_size"] == 3  # member + 2 guests
    assert body["fee_amount_kobo"] == 350_000 * 3  # server price; client ignored
    assert body["status"] == "confirmed"

    n_guests = (
        await db_session.execute(
            select(func.count())
            .select_from(BookingGuest)
            .where(BookingGuest.booking_id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert n_guests == 2


@pytest.mark.asyncio
async def test_capacity_rejects_overfill_by_heads(sessions_client, db_session):
    session = await _make_community_session(db_session, pool_fee=0, capacity=2)
    p_member, p_membership, p_wallet = _patch_member_wallet()
    with p_member, p_membership, p_wallet:
        # member + 2 guests = 3 heads > capacity 2 → 409
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "pay_with_bubbles": True,
                "guests": [{"full_name": "G1"}, {"full_name": "G2"}],
            },
        )
    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize(
    ("source", "member_fee_kobo", "expected_status"),
    [
        ("club_enrollment", 0, "confirmed"),
        ("club_transition", 500_000, "pending"),
        ("community_dropin", 650_000, "pending"),
    ],
)
@pytest.mark.asyncio
async def test_club_booking_uses_access_source_price_and_ignores_client_amount(
    sessions_client,
    db_session,
    source,
    member_fee_kobo,
    expected_status,
):
    from libs.common.session_access import SessionAccessDecision

    session = await _make_club_session(db_session)
    member_patch, _, _ = _patch_member_wallet()
    access = SessionAccessDecision(
        required_tier="club",
        visible=True,
        bookable=True,
        digest_eligible=True,
        prompt_eligible=True,
        sign_in_allowed=False,
        access_source=source,
        fee_amount_kobo=member_fee_kobo,
        price_label="Server price",
    )
    with (
        member_patch,
        patch(
            f"{BOOKINGS}.evaluate_member_session_access",
            AsyncMock(return_value=access),
        ),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": 1,
                "pay_with_bubbles": False,
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["fee_amount_kobo"] == member_fee_kobo
    assert response.json()["member_fee_amount_kobo"] == member_fee_kobo
    assert response.json()["access_source"] == source
    assert response.json()["status"] == expected_status


@pytest.mark.asyncio
async def test_transition_booking_snapshots_current_session_price(
    sessions_client, db_session
):
    from libs.common.session_access import evaluate_session_access
    from services.sessions_service.models import SessionBooking

    session = await _make_club_session(db_session, pool_fee=520_000)
    access = evaluate_session_access(
        {"id": str(uuid.uuid4()), "member_id": str(uuid.uuid4())},
        session,
        now=_start() - timedelta(days=1),
        club_access_result={
            "allowed": True,
            "source": "club_transition",
            "payment_mode": "transition_per_session",
            "fee_amount_kobo": None,
        },
    )
    member_patch, _, _ = _patch_member_wallet()
    with (
        member_patch,
        patch(
            f"{BOOKINGS}.evaluate_member_session_access",
            AsyncMock(return_value=access),
        ),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={"session_id": str(session.id), "fee_amount_kobo": 1},
        )

    assert response.status_code == 201, response.text
    booking_id = uuid.UUID(response.json()["id"])
    assert response.json()["fee_amount_kobo"] == 520_000
    assert response.json()["member_fee_amount_kobo"] == 520_000

    session.pool_fee = 550_000
    await db_session.commit()
    booking = await db_session.get(SessionBooking, booking_id)

    assert booking.fee_amount_kobo == 520_000
    assert booking.member_fee_amount_kobo == 520_000
    assert booking.access_source == "club_transition"


@pytest.mark.asyncio
async def test_attached_guest_fee_is_independent_from_included_club_member_price(
    sessions_client, db_session
):
    from libs.common.session_access import SessionAccessDecision

    session = await _make_club_session(db_session, guest_fee=700_000)
    member_patch, _, _ = _patch_member_wallet()
    access = SessionAccessDecision(
        required_tier="club",
        visible=True,
        bookable=True,
        digest_eligible=True,
        prompt_eligible=True,
        sign_in_allowed=False,
        access_source="club_enrollment",
        fee_amount_kobo=0,
        price_label="Included in Club quarter",
    )
    with (
        member_patch,
        patch(
            f"{BOOKINGS}.evaluate_member_session_access",
            AsyncMock(return_value=access),
        ),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": 1,
                "pay_with_bubbles": False,
                "guests": [{"full_name": "Guest-priced independently"}],
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["member_fee_amount_kobo"] == 0
    assert response.json()["fee_amount_kobo"] == 700_000


@pytest.mark.asyncio
async def test_minor_guest_without_guardian_rejected(sessions_client, db_session):
    session = await _make_community_session(db_session)
    p_member, p_membership, p_wallet = _patch_member_wallet()
    with p_member, p_membership, p_wallet:
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "pay_with_bubbles": True,
                "guests": [{"full_name": "Kid", "date_of_birth": "2018-01-01"}],
            },
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_guest_attendance_check_in(attendance_client, db_session):
    session_id = uuid.uuid4()
    booking_guest_id = uuid.uuid4()
    with patch(
        f"{SIGN_IN}.get_session_by_id",
        AsyncMock(return_value={"id": str(session_id), "title": "X", "pool_fee": 0}),
    ):
        resp = await attendance_client.post(
            f"/attendance/sessions/{session_id}/attendance/guest",
            json={"booking_guest_id": str(booking_guest_id), "status": "present"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "guest"
    assert body["member_id"] is None
    assert body["booking_guest_id"] == str(booking_guest_id)


@pytest.mark.asyncio
async def test_add_trial_guest_bumps_party_size_comped(sessions_client, db_session):
    """Phase 1.5: a coach adds a trial guest — party_size +1, fee unchanged."""
    from services.sessions_service.models import (
        BookingGuest,
        SessionBooking,
        SessionBookingStatus,
    )

    session = await _make_community_session(db_session, pool_fee=3500, capacity=10)
    booking = SessionBooking(
        session_id=session.id,
        member_id=uuid.uuid4(),
        member_auth_id="auth-trial",
        status=SessionBookingStatus.CONFIRMED,
        party_size=1,
        fee_amount_kobo=3500,
    )
    db_session.add(booking)
    await db_session.commit()
    await db_session.refresh(booking)

    # sessions_client wires require_coach → admin, so the approval gate passes.
    resp = await sessions_client.post(
        f"/sessions/bookings/{booking.id}/trial-guest",
        json={"full_name": "Trial Friend", "phone": "0900111222"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["party_size"] == 2  # member + trial
    assert body["fee_amount_kobo"] == 3500  # comped — fee unchanged

    n_trial = (
        await db_session.execute(
            select(func.count())
            .select_from(BookingGuest)
            .where(
                BookingGuest.booking_id == booking.id,
                BookingGuest.intent == "trial",
            )
        )
    ).scalar_one()
    assert n_trial == 1


@pytest.mark.asyncio
async def test_block_booking_placeholders_then_named(sessions_client, db_session):
    """Phase 2: reserve anonymous slots, then name one before check-in."""
    from services.sessions_service.models import BookingGuest

    session = await _make_community_session(db_session, pool_fee=0, capacity=10)
    p_member, p_membership, p_wallet = _patch_member_wallet()
    with p_member, p_membership, p_wallet:
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "pay_with_bubbles": True,
                "block_guests": 2,
            },
        )
    assert resp.status_code == 201, resp.text
    booking_id = uuid.UUID(resp.json()["id"])
    assert resp.json()["party_size"] == 3  # member + 2 anonymous slots

    placeholders = (
        (
            await db_session.execute(
                select(BookingGuest).where(BookingGuest.booking_id == booking_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(placeholders) == 2
    assert all(g.full_name is None for g in placeholders)

    # Name one placeholder (booking owner = same wired user).
    resp2 = await sessions_client.patch(
        f"/sessions/bookings/{booking_id}/guests/{placeholders[0].id}",
        json={"full_name": "Named Later", "phone": "0901"},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["full_name"] == "Named Later"


@pytest.mark.asyncio
async def test_guest_conversion_funnel(sessions_client, db_session):
    """Phase 3: leads list shows repeat guests by phone; convert links them."""
    from services.sessions_service.models import BookingGuest

    phone = f"+23480{uuid.uuid4().int % 100_000_000:08d}"
    for _ in range(2):  # two appearances, unconverted
        db_session.add(
            BookingGuest(
                booking_id=uuid.uuid4(),
                full_name="Repeat Guest",
                phone=phone,
                intent="social",
            )
        )
    await db_session.commit()

    leads = (await sessions_client.get("/sessions/booking-guests/leads")).json()
    match = next((row for row in leads if row["phone"] == phone), None)
    assert match is not None
    assert match["appearances"] == 2

    conv = await sessions_client.post(
        "/sessions/booking-guests/convert",
        json={"phone": phone, "member_id": str(uuid.uuid4())},
    )
    assert conv.status_code == 200, conv.text
    assert conv.json()["converted"] == 2

    leads2 = (await sessions_client.get("/sessions/booking-guests/leads")).json()
    assert all(row["phone"] != phone for row in leads2)  # converted → no longer a lead
