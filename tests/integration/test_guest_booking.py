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
SIGN_IN = "services.attendance_service.routers.member.sign_in"


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


def _patch_member_wallet():
    """Patch the member-lookup + wallet-debit the book endpoint imports."""
    return (
        patch(
            f"{BOOKINGS}.get_member_by_auth_id",
            AsyncMock(return_value={"id": str(uuid.uuid4()), "auth_id": "auth-x"}),
        ),
        patch(
            f"{BOOKINGS}.debit_member_wallet",
            AsyncMock(return_value={"transaction_id": str(uuid.uuid4())}),
        ),
    )


@pytest.mark.asyncio
async def test_book_with_guests_persists_and_computes_fee(sessions_client, db_session):
    from services.sessions_service.models import BookingGuest

    session = await _make_community_session(db_session, pool_fee=3500)
    p_member, p_wallet = _patch_member_wallet()
    with p_member, p_wallet:
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
    assert body["fee_amount_kobo"] == 3500 * 3  # server-computed, client value ignored
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
    p_member, p_wallet = _patch_member_wallet()
    with p_member, p_wallet:
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


@pytest.mark.asyncio
async def test_minor_guest_without_guardian_rejected(sessions_client, db_session):
    session = await _make_community_session(db_session)
    p_member, p_wallet = _patch_member_wallet()
    with p_member, p_wallet:
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
