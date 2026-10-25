from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionRate,
    SessionType,
)
from services.sessions_service.routers import member_prices as prices

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_batch_quote_honors_agreements_inclusions_and_saved_bookings(
    sessions_client, db_session, monkeypatch
):
    club, session_id = uuid4(), uuid4()
    ay, regular, prepaid, booked = [uuid4() for _ in range(4)]
    session = Session(
        id=session_id,
        session_type=SessionType.CLUB,
        club_id=club,
        title="VI practice",
        starts_at=utc_now() + timedelta(days=3),
        ends_at=utc_now() + timedelta(days=3, hours=2),
        pool_fee=1_500_000,
    )
    db_session.add(session)
    db_session.add(
        SessionRate(
            session_id=session_id,
            audience="club",
            rate_code="club_transition",
            access_source="club_transition",
            rate_mode="fixed",
            amount_kobo=1_500_000,
            label="Standard rate",
        )
    )
    booking = SessionBooking(
        session_id=session_id,
        member_id=booked,
        member_auth_id=str(booked),
        status="confirmed",
        fee_amount_kobo=1_500_000,
        member_fee_amount_kobo=1_500_000,
    )
    db_session.add(booking)
    await db_session.commit()
    access = AsyncMock(
        return_value={
            "ay": {
                "allowed": True,
                "source": "club_transition",
                "fee_amount_kobo": 1_000_000,
            },
            "regular": {
                "allowed": True,
                "source": "club_transition",
                "fee_amount_kobo": None,
            },
            "prepaid": {
                "allowed": True,
                "source": "club_enrollment",
                "fee_amount_kobo": 0,
            },
        }
    )
    monkeypatch.setattr(prices, "check_club_access_batch", access)
    response = await sessions_client.post(
        "/internal/sessions/member-prices",
        json={
            "checks": [
                {
                    "context_key": key,
                    "member_id": str(member),
                    "session_id": str(session_id),
                }
                for key, member in [
                    ("ay", ay),
                    ("regular", regular),
                    ("prepaid", prepaid),
                    ("booked", booked),
                ]
            ]
        },
    )
    assert response.status_code == 200, response.text
    by_key = {item["context_key"]: item for item in response.json()["items"]}
    assert by_key["ay"]["fee_amount_kobo"] == 1_000_000
    assert by_key["regular"]["fee_amount_kobo"] == 1_500_000
    assert by_key["prepaid"]["fee_amount_kobo"] == 0
    assert by_key["booked"]["fee_amount_kobo"] == 1_500_000
    assert by_key["booked"]["pricing_source"] == "booking"
    assert len(access.await_args.args[0]) == 3
    assert all(
        check["session_id"] == str(session_id) for check in access.await_args.args[0]
    )
    await db_session.refresh(booking)
    assert booking.fee_amount_kobo == 1_500_000
    assert (
        await db_session.execute(
            select(SessionBooking).where(SessionBooking.session_id == session_id)
        )
    ).scalars().all() == [booking]


async def test_missing_session_or_access_has_unknown_price(
    sessions_client, monkeypatch
):
    monkeypatch.setattr(prices, "check_club_access_batch", AsyncMock(return_value={}))
    response = await sessions_client.post(
        "/internal/sessions/member-prices",
        json={
            "checks": [
                {
                    "context_key": "missing",
                    "member_id": str(uuid4()),
                    "session_id": str(uuid4()),
                },
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["fee_amount_kobo"] is None
