from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import HTTPException

from services.members_service.routers import clubs
from services.members_service.schemas.club import ActivateClubApplicationRequest
from services.payments_service.routers.intents import intent_creation
from services.sessions_service.routers import guest_passes


def _plan(start: date, end: date):
    return SimpleNamespace(period_start=start, period_end=end)


def test_multi_quarter_selection_must_be_consecutive():
    clubs._assert_consecutive_plan_periods(
        [
            _plan(date(2026, 10, 1), date(2026, 12, 31)),
            _plan(date(2027, 1, 1), date(2027, 3, 31)),
        ]
    )

    with pytest.raises(HTTPException, match="must be consecutive"):
        clubs._assert_consecutive_plan_periods(
            [
                _plan(date(2026, 10, 1), date(2026, 12, 31)),
                _plan(date(2027, 4, 1), date(2027, 6, 30)),
            ]
        )


def test_multi_quarter_selection_rejects_overlap():
    with pytest.raises(HTTPException, match="overlap"):
        clubs._assert_consecutive_plan_periods(
            [
                _plan(date(2026, 10, 1), date(2026, 12, 31)),
                _plan(date(2026, 12, 1), date(2027, 2, 28)),
            ]
        )


def test_activation_contract_keeps_experience_amount():
    request = ActivateClubApplicationRequest(
        payment_reference="CLUB-TEST",
        community_experience_selected=True,
        community_experience_fee_kobo=3_000_000,
    )

    assert request.community_experience_fee_kobo == 3_000_000


@pytest.mark.asyncio
async def test_club_capacity_reservation_uses_payment_reference(monkeypatch):
    application_id = uuid.uuid4()
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://members/reservation"),
        json={
            "application_id": str(application_id),
            "payment_reference": "PAY-123",
            "status": "active",
            "expires_at": "2026-09-03T12:30:00Z",
            "plan_version_ids": [str(uuid.uuid4())],
        },
    )
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(intent_creation, "internal_post", post)

    result = await intent_creation._reserve_club_application_capacity(
        application_id,
        payment_reference="PAY-123",
    )

    assert result["status"] == "active"
    assert post.await_args.kwargs["json"] == {"payment_reference": "PAY-123"}


@pytest.mark.asyncio
async def test_club_capacity_conflict_is_returned_to_checkout(monkeypatch):
    response = httpx.Response(
        409,
        request=httpx.Request("POST", "http://members/reservation"),
        json={"detail": "Q4 Yaba has reached its Club capacity"},
    )
    monkeypatch.setattr(
        intent_creation,
        "internal_post",
        AsyncMock(return_value=response),
    )

    with pytest.raises(HTTPException) as exc_info:
        await intent_creation._reserve_club_application_capacity(
            uuid.uuid4(),
            payment_reference="PAY-FULL",
        )

    assert exc_info.value.status_code == 409
    assert "reached its Club capacity" in exc_info.value.detail


class _ScalarResult:
    def __init__(self, value: int):
        self.value = value

    def scalar_one(self):
        return self.value


class _RecordingDb:
    def __init__(self, values: list[int]):
        self.values = iter(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(next(self.values))


@pytest.mark.asyncio
async def test_guest_capacity_ignores_expired_pending_holds_in_query():
    db = _RecordingDb([2, 1])
    session = SimpleNamespace(id=uuid.uuid4(), capacity=5)

    remaining = await guest_passes._spaces_remaining(session, db)

    assert remaining == 2
    sql = " ".join(str(statement) for statement in db.statements)
    assert "session_bookings.expires_at" in sql
    assert "guest_passes.reservation_expires_at" in sql
    assert guest_passes.GUEST_PASS_RESERVATION_MINUTES == 30
