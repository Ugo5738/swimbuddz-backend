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
from unittest.mock import AsyncMock, patch

import pytest

from libs.common.currency import kobo_to_bubbles
from services.sessions_service.models import (
    BookingChannel,
    SessionBooking,
    SessionBookingStatus,
)
from tests.factories import SessionFactory

_BOOKINGS = "services.sessions_service.routers.bookings"
_DEBIT = f"{_BOOKINGS}.debit_member_wallet"
_RESOLVE_MEMBER = f"{_BOOKINGS}.get_member_by_auth_id"

POOL_FEE_KOBO = 350_000  # ₦3,500


async def _session(db_session, **overrides):
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
    with patch(_RESOLVE_MEMBER, _member_mock(member_id)), patch(_DEBIT, debit):
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
    with patch(_RESOLVE_MEMBER, _member_mock(member_id)), patch(_DEBIT, debit):
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
    with patch(_RESOLVE_MEMBER, _member_mock(member_id)), patch(_DEBIT, debit):
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
    with patch(_RESOLVE_MEMBER, _member_mock(member_id)), patch(_DEBIT, debit):
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
