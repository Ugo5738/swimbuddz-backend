"""Walk-in attendance corrections keep payment obligations truthful."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.sessions_service.models import SessionBookingStatus
from services.sessions_service.routers import internal
from services.sessions_service.schemas import WalkInAttendanceReconcileRequest


def _booking(*, status=SessionBookingStatus.CONFIRMED, paid=False, notes=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        booking_source="admin_walk_in",
        status=status,
        payment_intent_id=uuid.uuid4() if paid else None,
        wallet_transaction_id=None,
        cancelled_at=None,
        confirmed_at=None,
        notes=notes,
    )


def _db(booking):
    return SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: booking)
        ),
        commit=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_absent_cancels_unpaid_admin_walk_in():
    booking = _booking()
    db = _db(booking)

    result = await internal.reconcile_admin_walk_in_attendance(
        booking.session_id,
        WalkInAttendanceReconcileRequest(
            member_id=booking.member_id,
            status="absent",
        ),
        SimpleNamespace(),
        db,
    )

    assert result["action"] == "cancelled"
    assert booking.status == SessionBookingStatus.CANCELLED
    assert "[walk_in_attendance_reversed]" in booking.notes
    assert booking.cancelled_at is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_absent_never_cancels_paid_walk_in():
    booking = _booking(paid=True)
    db = _db(booking)

    result = await internal.reconcile_admin_walk_in_attendance(
        booking.session_id,
        WalkInAttendanceReconcileRequest(
            member_id=booking.member_id,
            status="absent",
        ),
        SimpleNamespace(),
        db,
    )

    assert result == {"action": "preserved", "reason": "paid_booking"}
    assert booking.status == SessionBookingStatus.CONFIRMED
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_present_restores_only_reconciliation_cancelled_walk_in():
    booking = _booking(
        status=SessionBookingStatus.CANCELLED,
        notes="[walk_in_attendance_reversed] status=absent",
    )
    db = _db(booking)

    result = await internal.reconcile_admin_walk_in_attendance(
        booking.session_id,
        WalkInAttendanceReconcileRequest(
            member_id=booking.member_id,
            status="present",
        ),
        SimpleNamespace(),
        db,
    )

    assert result["action"] == "restored"
    assert booking.status == SessionBookingStatus.CONFIRMED
    assert booking.cancelled_at is None
    assert booking.confirmed_at is not None
    db.commit.assert_awaited_once()
