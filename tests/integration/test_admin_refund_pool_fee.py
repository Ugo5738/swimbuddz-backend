"""Admin pool-fee refund — the make-up / rain-out path.

A member paid the per-session pool fee, was marked absent (e.g. rain), and is
owed it back so it funds a make-up. The admin refunds via
``POST /sessions/bookings/{id}/refund-pool-fee``, which MUST:
  - route through the *accounted* session_booking refund path
    (``transaction_type=refund``, ``reference_type=session_booking``) so the
    ledger reverses the pool-fee revenue — NOT the ledger-invisible
    "Adjust Bubbles" tool (``admin_adjustment``, which the emitter skips);
  - be idempotent per booking;
  - refuse unpaid / non-confirmed / zero-fee bookings.
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

_CREDIT = "services.sessions_service.routers.bookings.credit_member_wallet"


async def _booking(db_session, **overrides) -> SessionBooking:
    """A CONFIRMED, Bubble-paid booking with a ₦3,500 pool fee (override-able)."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    fields = dict(
        session_id=session.id,
        member_id=uuid.uuid4(),
        member_auth_id=str(uuid.uuid4()),
        status=SessionBookingStatus.CONFIRMED,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=350_000,  # ₦3,500
        wallet_transaction_id=uuid.uuid4(),  # paid via Bubbles
    )
    fields.update(overrides)
    booking = SessionBooking(**fields)
    db_session.add(booking)
    await db_session.commit()
    await db_session.refresh(booking)
    return booking


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_pool_fee_uses_accounted_session_booking_path(
    sessions_client, db_session
):
    booking = await _booking(db_session)
    with patch(
        _CREDIT, new=AsyncMock(return_value={"transaction_id": str(uuid.uuid4())})
    ) as credit:
        resp = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "Rained out — make-up agreed"},
        )

    assert resp.status_code == 200
    credit.assert_awaited_once()
    kwargs = credit.await_args.kwargs
    # The accounted path — NOT a generic admin_adjustment.
    assert kwargs["transaction_type"] == "refund"
    assert kwargs["reference_type"] == "session_booking"
    assert kwargs["reference_id"] == str(booking.id)
    assert kwargs["amount"] == kobo_to_bubbles(350_000)  # ₦3,500 -> 35 Bubbles
    assert kwargs["idempotency_key"] == f"booking-refund-{booking.id}"
    assert credit.await_args.args[0] == booking.member_auth_id

    body = resp.json()
    # Booking is NOT cancelled — it stays as the audit trail, now marked refunded.
    assert body["status"] == "confirmed"
    assert "[pool_fee_refunded_at:" in (body["notes"] or "")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_pool_fee_is_idempotent(sessions_client, db_session):
    booking = await _booking(db_session)
    with patch(_CREDIT, new=AsyncMock(return_value={})) as credit:
        first = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "rain"},
        )
        second = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "rain again"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    credit.assert_awaited_once()  # second call short-circuits on the notes marker


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_pool_fee_refuses_unpaid_booking(sessions_client, db_session):
    # CONFIRMED but never paid (no wallet txn, no payment intent) — e.g. an
    # unpaid admin walk-in. Refunding would hand out money never collected.
    booking = await _booking(
        db_session, wallet_transaction_id=None, payment_intent_id=None
    )
    with patch(_CREDIT, new=AsyncMock()) as credit:
        resp = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "x"},
        )
    assert resp.status_code == 422
    credit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_pool_fee_refuses_non_confirmed(sessions_client, db_session):
    # A cancelled booking was already refunded via /cancel — don't double-refund.
    booking = await _booking(db_session, status=SessionBookingStatus.CANCELLED)
    with patch(_CREDIT, new=AsyncMock()) as credit:
        resp = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "x"},
        )
    assert resp.status_code == 422
    credit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refund_pool_fee_refuses_zero_fee(sessions_client, db_session):
    booking = await _booking(db_session, fee_amount_kobo=0)
    with patch(_CREDIT, new=AsyncMock()) as credit:
        resp = await sessions_client.post(
            f"/sessions/bookings/{booking.id}/refund-pool-fee",
            json={"reason": "x"},
        )
    assert resp.status_code == 422
    credit.assert_not_awaited()
