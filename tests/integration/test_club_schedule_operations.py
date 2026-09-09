"""PostgreSQL regression: a moved swim cannot rewrite a paid booking."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from libs.auth.models import AuthUser
from services.sessions_service.models import (
    ClubScheduleOperation,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
)
from services.sessions_service.routers import club_operations as ops


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paid_booking_survives_same_identity_makeup_and_retry(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    swim = Session(
        title="Paid quarter swim",
        session_type=SessionType.CLUB,
        club_id=uuid4(),
        pool_id=uuid4(),
        club_access_mode="plan_included",
        starts_at=now + timedelta(days=10),
        ends_at=now + timedelta(days=10, hours=1),
        status=SessionStatus.SCHEDULED,
        published_at=now,
        pool_fee=550000,
        capacity=20,
    )
    db_session.add(swim)
    await db_session.flush()
    intent = uuid4()
    booking = SessionBooking(
        session_id=swim.id,
        member_id=uuid4(),
        member_auth_id=str(uuid4()),
        status=SessionBookingStatus.CONFIRMED,
        payment_intent_id=intent,
        member_fee_amount_kobo=520000,
        access_source="club_transition",
    )
    db_session.add(booking)
    await db_session.commit()
    monkeypatch.setattr(
        ops, "members_operation", AsyncMock(return_value={"published_promise": True})
    )
    monkeypatch.setattr(ops, "notify_reschedule", AsyncMock())
    user = AuthUser(sub=str(uuid4()), app_metadata={"roles": ["admin"]})
    body = ops.ReschedulePractice(
        operation_id=uuid4(),
        starts_at=swim.starts_at + timedelta(days=2),
        reason="Rain makeup",
        pool_time_confirmed=True,
    )
    await ops.reschedule_practice(swim.id, body, user, db_session)
    await ops.reschedule_practice(swim.id, body, user, db_session)
    await db_session.refresh(booking)
    await db_session.refresh(swim)
    assert booking.session_id == swim.id
    assert (
        booking.member_fee_amount_kobo == 520000 and booking.payment_intent_id == intent
    )
    assert booking.status == SessionBookingStatus.CONFIRMED and swim.pool_fee == 550000
    assert swim.starts_at == body.starts_at
    assert (
        await db_session.scalar(
            select(func.count(ClubScheduleOperation.id)).where(
                ClubScheduleOperation.id == body.operation_id
            )
        )
        == 1
    )
