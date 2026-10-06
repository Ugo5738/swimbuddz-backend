"""book_session reconciliation — the abandoned / re-book fix.

A member who starts a Paystack booking gets a PENDING SessionBooking with a
15-min TTL *before* paying. If they abandon checkout and later re-book with
Bubbles, the endpoint must drive that existing row to CONFIRMED (debiting the
wallet). It used to short-circuit with ``return existing`` *before* the debit
ran, leaving the booking PENDING and unpaid — so no Bubbles were ever debited
and the admin attendance report (CONFIRMED-only) never showed the member.

These tests pin the reconcile behaviour against the single (session, member)
row the unique constraint allows: confirm-or-revive it, debit exactly once,
and never re-charge a booking that is already CONFIRMED.
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from libs.common.currency import kobo_to_bubbles
from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    BookingChannel,
    SessionBooking,
    SessionBookingStatus,
)
from tests.factories import SessionFactory

_BOOKINGS = "services.sessions_service.routers.bookings"
_SESSION_ACCESS = "services.sessions_service.services.session_access"
_DEBIT = f"{_BOOKINGS}.debit_member_wallet"
_RESOLVE_MEMBER = f"{_BOOKINGS}.get_member_by_auth_id"
_MEMBERSHIP = f"{_SESSION_ACCESS}.get_member_membership"
_CLUB_ACCESS = f"{_SESSION_ACCESS}.check_club_access_batch"
_ATTENDANCE = f"{_BOOKINGS}.sync_booking_attendance"

POOL_FEE_KOBO = 350_000  # ₦3,500


@pytest.mark.integration
@pytest.mark.parametrize(
    "fee_mode,expected_fee,expected_status",
    [("included", 0, "confirmed"), ("paid_extra", 1500000, "pending")],
)
async def test_cohort_tuition_and_extra_class_persist_distinct_booking_prices(
    sessions_client, db_session, fee_mode, expected_fee, expected_status
):
    """CI/PostgreSQL coverage: nonzero pool cost alone never bills tuition twice."""
    from sqlalchemy import select

    member_id = uuid.uuid4()
    session = await _session(
        db_session, cohort_id=uuid.uuid4(), pool_fee=1500000, cohort_fee_mode=fee_mode
    )
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(
            f"{_SESSION_ACCESS}.check_cohort_enrollment",
            AsyncMock(return_value={"enrolled": True}),
        ),
        patch(f"{_BOOKINGS}.queue_confirmation", AsyncMock(return_value="email-key")),
        patch(f"{_BOOKINGS}.deliver_confirmation", AsyncMock()),
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
    body = response.json()
    assert body["status"] == expected_status
    assert body["fee_amount_kobo"] == expected_fee
    assert body["member_fee_amount_kobo"] == expected_fee
    assert body["access_source"] == "cohort_enrollment"
    persisted = (
        await db_session.execute(
            select(SessionBooking).where(SessionBooking.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert persisted.fee_amount_kobo == expected_fee
    assert persisted.payment_intent_id is None
    assert persisted.wallet_transaction_id is None


async def _legacy_club_access_mock(checks, **kwargs):
    return {
        check["context_key"]: {
            "context_key": check["context_key"],
            "allowed": True,
            "source": "legacy_club_entitlement",
            "enrollment_id": None,
            "club_id": None,
            "payment_mode": None,
            "fee_amount_kobo": None,
        }
        for check in checks
    }


@pytest.mark.integration
@pytest.mark.parametrize("club,expected", [(False, 1_250_000), (True, 1_000_000)])
async def test_event_rebooking_uses_current_member_rate_and_independent_guest_rate(
    sessions_client, db_session, club, expected
):
    member_id = uuid.uuid4()
    session = await _session(
        db_session,
        event_id=uuid.uuid4(),
        pool_fee=1_000_000,
        community_dropin_fee_kobo=1_250_000,
        guest_fee_kobo=1_500_000,
        allows_guests=True,
        max_guests_per_booking=2,
    )
    expired = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=SessionBookingStatus.EXPIRED,
        member_fee_amount_kobo=1_000_000,
    )
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(
            _MEMBERSHIP,
            AsyncMock(
                return_value={
                    "community_paid_until": "2035-01-01T00:00:00+00:00",
                    "club_paid_until": "2035-01-01T00:00:00+00:00" if club else None,
                }
            ),
        ),
        patch(
            f"{_SESSION_ACCESS}.check_event_attendance_batch",
            AsyncMock(
                return_value={
                    str(session.id): {"allowed": True, "source": "event_public"},
                }
            ),
        ),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": 1,
                "pay_with_bubbles": False,
                "block_guests": 1,
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["id"] == str(expired.id)
    assert response.json()["member_fee_amount_kobo"] == expected
    assert response.json()["fee_amount_kobo"] == expected + 1_500_000
    await db_session.refresh(expired)
    assert expired.member_fee_amount_kobo == expected
    assert expired.status == SessionBookingStatus.PENDING


@pytest.fixture(autouse=True)
def _stub_session_dependencies():
    with (
        patch(_ATTENDANCE, AsyncMock(return_value=None)),
        patch(
            _CLUB_ACCESS,
            AsyncMock(side_effect=_legacy_club_access_mock),
        ),
    ):
        yield


async def _session(db_session, **overrides):
    # The booking endpoint uses the server-side session fee and ignores
    # fee_amount_kobo sent by the client.
    overrides.setdefault("pool_fee", POOL_FEE_KOBO)

    session = SessionFactory.create(**overrides)  # default: non-cohort CLUB session
    db_session.add(session)
    await db_session.commit()
    return session


async def _booking(db_session, *, session_id, member_id, status, **overrides):
    fields = dict(
        session_id=session_id,
        member_id=member_id,
        member_auth_id=str(uuid.uuid4()),
        status=status,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=POOL_FEE_KOBO,
    )
    fields.update(overrides)
    booking = SessionBooking(**fields)
    db_session.add(booking)
    await db_session.commit()
    await db_session.refresh(booking)
    return booking


def _member_mock(member_id):
    """Mock get_member_by_auth_id so _resolve_member_for_user yields member_id."""
    return AsyncMock(return_value={"id": str(member_id), "auth_id": str(uuid.uuid4())})


def _club_membership_mock(member_id):
    """Mock active club membership for default club SessionFactory rows."""
    return AsyncMock(
        return_value={
            "member_id": str(member_id),
            "primary_tier": "club",
            "active_tiers": ["club", "community"],
            "community_paid_until": "2035-01-01T00:00:00+00:00",
            "club_paid_until": "2035-01-01T00:00:00+00:00",
            "academy_paid_until": None,
        }
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_walk_in_preserves_cross_location_visit_price_and_source(
    sessions_client, db_session
):
    member_id = uuid.uuid4()
    member_auth_id = str(uuid.uuid4())
    session = await _session(
        db_session,
        club_id=uuid.uuid4(),
        pool_fee=1_200_000,
        visiting_club_fee_kobo=850_000,
        allows_visiting_club_members=True,
        club_access_mode="plan_included",
    )

    async def visitor_access(checks, **kwargs):
        return {
            check["context_key"]: {
                "context_key": check["context_key"],
                "allowed": True,
                "source": "club_visit",
                "enrollment_id": str(uuid.uuid4()),
                "club_id": str(uuid.uuid4()),
                "payment_mode": "quarterly_prepaid",
                "fee_amount_kobo": None,
            }
            for check in checks
        }

    with (
        patch(
            "libs.common.service_client.get_member_by_id",
            AsyncMock(
                return_value={
                    "id": str(member_id),
                    "auth_id": member_auth_id,
                }
            ),
        ),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(_CLUB_ACCESS, AsyncMock(side_effect=visitor_access)),
        patch(f"{_BOOKINGS}._record_walk_in_attendance", AsyncMock()),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/admin/walk-in",
            json={"member_id": str(member_id)},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["fee_amount_kobo"] == 850_000
    assert body["member_fee_amount_kobo"] == 850_000
    assert body["access_source"] == "club_visit"

    # booking_source is an internal operational attribution field and is not
    # part of the public booking response contract; assert it on persistence.
    persisted = (
        await db_session.execute(
            select(SessionBooking).where(SessionBooking.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert persisted.access_source == "club_visit"
    assert persisted.booking_source == "admin_walk_in"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "status", [SessionBookingStatus.PENDING, SessionBookingStatus.EXPIRED]
)
@pytest.mark.parametrize(
    "payment_link", [None, "payment_intent_id", "wallet_transaction_id"]
)
async def test_admin_walk_in_recovers_reservation_without_repricing_or_charging(
    sessions_client, db_session, status, payment_link
):
    member_id = uuid.uuid4()
    session = await _session(
        db_session,
        event_id=uuid.uuid4(),
        pool_fee=1_250_000,
        starts_at=utc_now() - timedelta(days=3),
        ends_at=utc_now() - timedelta(days=3, hours=-1),
    )
    linked_payment = uuid.uuid4() if payment_link else None
    original = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=status,
        fee_amount_kobo=1_000_000,
        member_fee_amount_kobo=1_000_000,
        expires_at=utc_now() - timedelta(days=3),
        notes="Original reservation",
        **({payment_link: linked_payment} if payment_link else {}),
    )
    original_booked_at = original.booked_at
    attendance = AsyncMock()
    debit = AsyncMock()
    with (
        patch("libs.common.service_client.get_member_by_id", _member_mock(member_id)),
        patch(f"{_BOOKINGS}._record_walk_in_attendance", attendance),
        patch(_DEBIT, debit),
    ):
        for _ in range(2):
            response = await sessions_client.post(
                f"/sessions/{session.id}/admin/walk-in",
                json={"member_id": str(member_id), "fee_amount_kobo": 1_250_000},
            )
            assert response.status_code == 201, response.text
            assert response.json()["id"] == str(original.id)

    await db_session.refresh(original)
    assert original.status == SessionBookingStatus.CONFIRMED
    assert original.channel == BookingChannel.ADMIN
    assert original.expires_at is None
    assert original.confirmed_at is not None
    assert original.booked_at == original_booked_at
    assert original.fee_amount_kobo == 1_000_000
    assert original.member_fee_amount_kobo == 1_000_000
    assert original.notes == "Original reservation"
    assert original.payment_intent_id == (
        linked_payment if payment_link == "payment_intent_id" else None
    )
    assert original.wallet_transaction_id == (
        linked_payment if payment_link == "wallet_transaction_id" else None
    )
    rows = (
        (
            await db_session.execute(
                select(SessionBooking).where(
                    SessionBooking.session_id == session.id,
                    SessionBooking.member_id == member_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    debit.assert_not_awaited()
    assert attendance.await_count == 2
    attendance.assert_awaited_with(session.id, member_id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_walk_in_does_not_revive_cancelled_booking(
    sessions_client, db_session
):
    member_id = uuid.uuid4()
    session = await _session(db_session)
    cancelled = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=SessionBookingStatus.CANCELLED,
    )
    attendance = AsyncMock()
    with (
        patch("libs.common.service_client.get_member_by_id", _member_mock(member_id)),
        patch(f"{_BOOKINGS}._record_walk_in_attendance", attendance),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/admin/walk-in",
            json={"member_id": str(member_id), "fee_amount_kobo": POOL_FEE_KOBO},
        )
    assert response.status_code == 409, response.text
    await db_session.refresh(cancelled)
    assert cancelled.status == SessionBookingStatus.CANCELLED
    attendance.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rebook_existing_pending_with_bubbles_confirms_and_debits(
    sessions_client, db_session
):
    """The reported bug: stale PENDING + pay-with-Bubbles must debit + confirm."""
    member_id = uuid.uuid4()
    session = await _session(db_session)
    pending = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=SessionBookingStatus.PENDING,
    )

    debit = AsyncMock(return_value={"transaction_id": str(uuid.uuid4())})
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(_DEBIT, debit),
    ):
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": POOL_FEE_KOBO,
                "pay_with_bubbles": True,
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Same row, now CONFIRMED and Bubble-paid — NOT a second booking.
    assert body["id"] == str(pending.id)
    assert body["status"] == "confirmed"
    assert body["wallet_transaction_id"] is not None
    assert body["expires_at"] is None

    debit.assert_awaited_once()
    assert debit.await_args.kwargs["amount"] == kobo_to_bubbles(POOL_FEE_KOBO)
    assert debit.await_args.kwargs["reference_type"] == "session_booking"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rebook_confirmed_is_idempotent_and_never_recharges(
    sessions_client, db_session
):
    """A duplicate submit on a CONFIRMED booking returns it without re-charging."""
    member_id = uuid.uuid4()
    session = await _session(db_session)
    confirmed = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=SessionBookingStatus.CONFIRMED,
        wallet_transaction_id=uuid.uuid4(),
    )

    debit = AsyncMock(return_value={"transaction_id": str(uuid.uuid4())})
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(_DEBIT, debit),
    ):
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": POOL_FEE_KOBO,
                "pay_with_bubbles": True,
            },
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == str(confirmed.id)
    assert resp.json()["status"] == "confirmed"
    debit.assert_not_awaited()  # already paid — no double charge


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rebook_expired_revives_instead_of_409(sessions_client, db_session):
    """A dead (EXPIRED) row is revived in place — no 'contact support' 409."""
    member_id = uuid.uuid4()
    session = await _session(db_session)
    expired = await _booking(
        db_session,
        session_id=session.id,
        member_id=member_id,
        status=SessionBookingStatus.EXPIRED,
    )

    debit = AsyncMock(return_value={"transaction_id": str(uuid.uuid4())})
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(_DEBIT, debit),
    ):
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": POOL_FEE_KOBO,
                "pay_with_bubbles": True,
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == str(expired.id)  # same row, revived
    assert body["status"] == "confirmed"
    debit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_paystack_booking_creates_pending_without_debit(
    sessions_client, db_session
):
    """No prior row + Paystack path → fresh PENDING with TTL, wallet untouched."""
    member_id = uuid.uuid4()
    session = await _session(db_session)

    debit = AsyncMock(return_value={"transaction_id": str(uuid.uuid4())})
    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
        patch(_DEBIT, debit),
    ):
        resp = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "fee_amount_kobo": POOL_FEE_KOBO,
                "pay_with_bubbles": False,
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["expires_at"] is not None
    debit.assert_not_awaited()  # Paystack path — wallet untouched


@pytest.mark.asyncio
@pytest.mark.integration
async def test_free_booking_confirms_without_client_bubbles_flag(
    sessions_client, db_session
):
    member_id = uuid.uuid4()
    session = await _session(db_session, pool_fee=0)

    with (
        patch(_RESOLVE_MEMBER, _member_mock(member_id)),
        patch(_MEMBERSHIP, _club_membership_mock(member_id)),
    ):
        response = await sessions_client.post(
            f"/sessions/{session.id}/book",
            json={
                "session_id": str(session.id),
                "pay_with_bubbles": False,
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "confirmed"
    assert response.json()["wallet_transaction_id"] is None
