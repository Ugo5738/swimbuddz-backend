from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import HTTPException

from services.members_service.routers import clubs
from services.members_service.schemas.club import ActivateClubApplicationRequest
from services.members_service.services.club_access import resolve_club_access_checks
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


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _AccessDb:
    def __init__(self, memberships, enrollments):
        self.results = iter([_RowsResult(memberships), _RowsResult(enrollments)])

    async def execute(self, _statement):
        return next(self.results)


@pytest.mark.asyncio
async def test_club_access_uses_the_plan_pool_snapshot():
    member_id = uuid.uuid4()
    club_id = uuid.uuid4()
    enrollment_id = uuid.uuid4()
    original_pool_id = uuid.uuid4()
    changed_club_default = uuid.uuid4()
    now = datetime.now(timezone.utc)
    enrollment = SimpleNamespace(
        id=enrollment_id,
        member_id=member_id,
        club_id=club_id,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=30),
    )
    club = SimpleNamespace(default_pool_id=changed_club_default)
    plan = SimpleNamespace(pool_id=original_pool_id)
    checks = [
        SimpleNamespace(
            context_key="original",
            member_id=member_id,
            at=now,
            pool_id=original_pool_id,
            pod_id=None,
        ),
        SimpleNamespace(
            context_key="changed-default",
            member_id=member_id,
            at=now,
            pool_id=changed_club_default,
            pod_id=None,
        ),
    ]

    result = await resolve_club_access_checks(
        _AccessDb([], [(enrollment, club, plan)]),
        checks,
    )

    assert result[0]["allowed"] is True
    assert result[0]["source"] == "club_enrollment"
    assert result[1]["allowed"] is False


@pytest.mark.asyncio
async def test_transition_access_is_dated_and_location_specific_without_own_rate():
    member_id = uuid.uuid4()
    club_id = uuid.uuid4()
    pool_id = uuid.uuid4()
    another_pool_id = uuid.uuid4()
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    enrollment = SimpleNamespace(
        id=uuid.uuid4(),
        member_id=member_id,
        club_id=club_id,
        pool_id=pool_id,
        payment_mode="transition_per_session",
        starts_at=now,
        ends_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    club = SimpleNamespace(default_pool_id=another_pool_id)
    plan = SimpleNamespace(pool_id=another_pool_id)
    checks = [
        SimpleNamespace(
            context_key="before",
            member_id=member_id,
            at=now - timedelta(seconds=1),
            pool_id=pool_id,
            pod_id=None,
        ),
        SimpleNamespace(
            context_key="covered",
            member_id=member_id,
            at=now + timedelta(days=1),
            pool_id=pool_id,
            pod_id=None,
        ),
        SimpleNamespace(
            context_key="wrong-pool",
            member_id=member_id,
            at=now + timedelta(days=1),
            pool_id=another_pool_id,
            pod_id=None,
        ),
        SimpleNamespace(
            context_key="expired",
            member_id=member_id,
            at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            pool_id=pool_id,
            pod_id=None,
        ),
    ]

    result = await resolve_club_access_checks(
        _AccessDb([], [(enrollment, club, plan)]), checks
    )

    assert result[0]["allowed"] is False
    assert result[1] == {
        "context_key": "covered",
        "allowed": True,
        "source": "club_transition",
        "enrollment_id": enrollment.id,
        "club_id": club_id,
        "payment_mode": "transition_per_session",
        "fee_amount_kobo": None,
    }
    assert result[2]["allowed"] is False
    assert result[3]["allowed"] is False
