from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from libs.auth.models import AuthUser
from services.sessions_service.models import (
    ClubScheduleOperation,
    Session,
    SessionStatus,
    SessionTemplate,
    SessionType,
)
from services.sessions_service.routers import club_operations as ops, club_schedule
from services.sessions_service.services import club_generation as generation
from services.members_service.services.club_access import resolve_club_access_checks
from services.members_service.routers.internal._schemas import ClubAccessCheck
from tests.unit.test_club_capacity_and_guest_holds import _AccessDb


def result(rows=()):
    return NS(
        scalars=lambda: NS(
            all=lambda: list(rows),
            first=lambda: next(iter(rows), None),
            __iter__=lambda: iter(rows),
        ),
        scalar_one_or_none=lambda: next(iter(rows), None),
        first=lambda: next(iter(rows), None),
        all=lambda: list(rows),
    )


def template(**kw):
    return NS(
        id=uuid4(),
        club_id=uuid4(),
        pool_id=uuid4(),
        pod_id=None,
        title="Yaba practice",
        description=None,
        location_name="Pool",
        day_of_week=5,
        start_time=time(9),
        duration_minutes=90,
        capacity=20,
        session_type=SessionType.CLUB,
        club_access_mode="plan_included",
        is_active=True,
        pool_fee=99999999,
        pricing_settings={
            "pricing_expected_attendees": 10,
            "margin_type": "fixed_per_attendee",
            "margin_value": 1000,
            "expected_staff": 0,
            "lanes": 1,
            "cost_lines": [],
        },
        **kw,
    )


def quote(cost):
    return NS(
        status_code=200,
        json=lambda: {
            "currency": "NGN",
            "warnings": [],
            "lines": [
                {
                    "category": "pool",
                    "description": "Effective pool rate",
                    "charge_basis": "per_attendee",
                    "unit_cost_naira": cost,
                    "quantity": 10,
                    "source_rate_id": str(uuid4()),
                },
                {
                    "category": "refreshments",
                    "description": "Inherited refreshments",
                    "charge_basis": "per_attendee",
                    "unit_cost_naira": 500,
                    "quantity": 10,
                    "source_rate_id": str(uuid4()),
                },
            ],
        },
    )


def test_template_recurrence_has_thirteen_q4_swims_and_explicit_exclusions():
    days = list(
        generation.recurrence_dates(template(), date(2026, 10, 1), date(2026, 12, 31))
    )
    assert len(days) == 13
    assert date(2026, 12, 5) in days
    assert (
        len(
            list(
                generation.recurrence_dates(
                    template(),
                    date(2026, 10, 1),
                    date(2026, 12, 31),
                    {date(2026, 12, 5)},
                )
            )
        )
        == 12
    )


@pytest.mark.asyncio
async def test_new_dates_use_inherited_effective_pool_rates_not_old_manual_fee(
    monkeypatch,
):
    calls = AsyncMock(side_effect=[quote(3000), quote(4000)])
    monkeypatch.setattr(generation, "internal_post", calls)
    t = template()
    first = await generation.club_session_from_template(t, date(2026, 10, 3))
    second = await generation.club_session_from_template(t, date(2026, 10, 10))
    assert (first.pool_fee, second.pool_fee) == (450000, 550000)
    assert first.club_id == t.club_id and first.template_id == t.id
    assert first.status == SessionStatus.DRAFT and first.published_at is None
    assert calls.call_args_list[0].kwargs["json"]["activity_scope"] == "club"
    assert first.cost_lines[1]["category"] == "refreshments"


@pytest.mark.asyncio
async def test_inaugural_generation_needs_no_existing_session_and_is_idempotent(
    monkeypatch,
):
    objects = {}
    added = []

    async def get(model, id):
        return objects.get((model, id))

    def add(row):
        objects[(type(row), row.id)] = row
        added.append(row)

    db = NS(
        execute=AsyncMock(),
        get=AsyncMock(side_effect=get),
        add=add,
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        generation, "internal_post", AsyncMock(return_value=quote(3000))
    )
    monkeypatch.setattr(club_schedule, "query_schedule", AsyncMock(return_value=[]))
    body = club_schedule.GenerateQuarter(
        club_id=uuid4(),
        pool_id=uuid4(),
        title="First quarter",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        weekday=5,
        starts_at_local=time(9),
        duration_minutes=90,
        pricing_settings=template().pricing_settings,
    )
    await club_schedule.generate_quarter(body, db)
    sessions = [row for row in added if isinstance(row, Session)]
    assert (
        len(sessions) == 13
        and len([row for row in added if isinstance(row, SessionTemplate)]) == 1
    )
    assert all(
        row.status == SessionStatus.DRAFT and row.club_id == body.club_id
        for row in sessions
    )
    first_ids = [row.id for row in sessions]
    await club_schedule.generate_quarter(body, db)
    assert [row.id for row in added if isinstance(row, Session)] == first_ids


@pytest.mark.parametrize(
    "mode,payment,expected,source",
    [
        ("plan_included", "quarterly_prepaid", False, "none"),
        ("active_club", "quarterly_prepaid", True, "club_enrollment"),
        ("paid_addon", "quarterly_prepaid", True, "club_paid_addon"),
        ("active_club", "transition_per_session", True, "club_transition"),
    ],
)
@pytest.mark.asyncio
async def test_extra_practice_access_is_separate_from_quarter_inclusion(
    mode, payment, expected, source
):
    member, club, pool = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    enrollment = NS(
        id=uuid4(),
        member_id=member,
        club_id=club,
        pool_id=pool,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=90),
        payment_mode=payment,
    )
    plan = NS(pool_id=pool, session_links=[NS(session_id=uuid4(), pool_id=pool)])
    check = ClubAccessCheck(
        context_key="extra",
        member_id=member,
        club_id=club,
        pool_id=pool,
        session_id=uuid4(),
        club_access_mode=mode,
        at=now,
    )
    got = (
        await resolve_club_access_checks(
            _AccessDb([], [(enrollment, NS(default_pool_id=pool), plan)]), [check]
        )
    )[0]
    assert got["allowed"] is expected and got["source"] == source
    if mode == "paid_addon" or payment == "transition_per_session":
        assert got["fee_amount_kobo"] is None
    other_club = check.model_copy(update={"club_id": uuid4()})
    assert not (
        await resolve_club_access_checks(
            _AccessDb([], [(enrollment, NS(default_pool_id=pool), plan)]), [other_club]
        )
    )[0]["allowed"]


def session():
    now = datetime.now(timezone.utc) + timedelta(days=10)
    return NS(
        id=uuid4(),
        title="Promised swim",
        session_type=SessionType.CLUB,
        club_id=uuid4(),
        club_access_mode="plan_included",
        pod_id=None,
        pool_id=uuid4(),
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status=SessionStatus.SCHEDULED,
        timezone="Africa/Lagos",
        capacity=20,
        pool_fee=520000,
        pricing_mode="cost_plus",
        cost_lines=[],
        estimated_cost_per_attendee=400000,
        margin_amount_per_attendee=120000,
        pricing_expected_attendees=20,
    )


@pytest.mark.asyncio
async def test_reschedule_preserves_identity_current_price_and_paid_booking(
    monkeypatch,
):
    swim = session()
    booking = NS(
        session_id=swim.id,
        member_fee_amount_kobo=500000,
        payment_reference="already-paid",
    )
    objects = {}
    writes = []

    def add(row):
        objects[row.id] = row
        writes.append(row)

    db = NS(
        execute=AsyncMock(return_value=result([swim])),
        get=AsyncMock(side_effect=lambda model, id: objects.get(id)),
        add=add,
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        ops, "members_operation", AsyncMock(return_value={"published_promise": True})
    )
    monkeypatch.setattr(ops, "notify_reschedule", AsyncMock())
    user = AuthUser(sub=str(uuid4()), app_metadata={"roles": ["admin"]})
    body = ops.ReschedulePractice(
        operation_id=uuid4(),
        starts_at=swim.starts_at + timedelta(days=2),
        reason="Rain avoidance",
        pool_time_confirmed=True,
    )
    original_id, original_fee = swim.id, swim.pool_fee
    await ops.reschedule_practice(swim.id, body, user, db)
    await ops.reschedule_practice(swim.id, body, user, db)
    assert (
        swim.id == booking.session_id == original_id and swim.pool_fee == original_fee
    )
    assert (
        swim.starts_at == body.starts_at
        and swim.ends_at - swim.starts_at == timedelta(hours=1)
    )
    assert (
        booking.member_fee_amount_kobo == 500000
        and booking.payment_reference == "already-paid"
    )
    assert len(writes) == 1 and isinstance(writes[0], ClubScheduleOperation)
    assert writes[0].before[0]["starts_at"] != writes[0].after[0]["starts_at"]


@pytest.mark.asyncio
async def test_pod_lead_cannot_move_another_pod_or_general_swim(monkeypatch):
    swim = session()
    db = NS(execute=AsyncMock(return_value=result([swim])))
    user = AuthUser(sub=str(uuid4()))
    body = ops.ReschedulePractice(
        operation_id=uuid4(),
        starts_at=swim.starts_at + timedelta(days=2),
        reason="Rain avoidance",
        pool_time_confirmed=True,
    )
    with pytest.raises(HTTPException, match="Only Admin"):
        await ops.reschedule_practice(swim.id, body, user, db)
    swim.pod_id = uuid4()
    monkeypatch.setattr(
        ops, "pod_authority", AsyncMock(side_effect=HTTPException(403, "Another pod"))
    )
    with pytest.raises(HTTPException, match="Another pod"):
        await ops.reschedule_practice(swim.id, body, user, db)


def test_lead_requests_cannot_supply_price_pool_or_quarter_inclusion():
    base = dict(
        operation_id=uuid4(),
        pod_id=uuid4(),
        starts_at=datetime.now(timezone.utc),
        pool_time_confirmed=True,
    )
    for field, value in (
        ("pool_fee", 0),
        ("pool_id", str(uuid4())),
        ("club_access_mode", "plan_included"),
    ):
        with pytest.raises(ValidationError):
            ops.ExtraPractice(**base, **{field: value})


@pytest.mark.parametrize("change", ["quarter", "pool"])
@pytest.mark.asyncio
async def test_published_swim_cannot_be_moved_outside_its_commercial_promise(change):
    from services.members_service.routers import club_operations_internal as authority

    pool = uuid4()
    plan = NS(id=uuid4(), period_start=date(2026, 10, 1), period_end=date(2026, 12, 31))
    db = NS(execute=AsyncMock(return_value=result([(plan, NS(pool_id=pool))])))
    body = authority.PromiseCheck(
        session_id=uuid4(),
        starts_at=datetime(
            2027 if change == "quarter" else 2026, 12, 5, 9, tzinfo=timezone.utc
        ),
        pool_id=uuid4() if change == "pool" else pool,
    )
    with pytest.raises(HTTPException) as error:
        await authority.check_promises(body, db)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_reschedule_refusal_does_not_mutate_the_swim(monkeypatch):
    swim = session()
    before = (swim.starts_at, swim.ends_at, swim.pool_fee)
    db = NS(
        execute=AsyncMock(return_value=result([swim])),
        get=AsyncMock(return_value=None),
        add=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        ops,
        "members_operation",
        AsyncMock(side_effect=HTTPException(409, "Outside purchased quarter")),
    )
    user = AuthUser(sub=str(uuid4()), app_metadata={"roles": ["admin"]})
    body = ops.ReschedulePractice(
        operation_id=uuid4(),
        starts_at=swim.starts_at + timedelta(days=2),
        reason="Heavy rain",
        pool_time_confirmed=True,
    )
    with pytest.raises(HTTPException):
        await ops.reschedule_practice(swim.id, body, user, db)
    assert (swim.starts_at, swim.ends_at, swim.pool_fee) == before
    db.commit.assert_not_awaited()
