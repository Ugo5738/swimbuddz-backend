from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.members_service.routers.club_plan_admin import (
    next_quarter,
    suggested_experience_day,
    publish_draft,
)
from services.members_service.routers import club_plan_admin
from services.members_service.services import club_plan_schedule as schedule
from services.members_service.services.club_access import resolve_club_access_checks
from tests.club_schedule_helpers import attach_schedule
from tests.unit.test_club_capacity_and_guest_holds import _AccessDb


def quarter(count=13, amount=6_500_000):
    return attach_schedule(
        SimpleNamespace(
            period_start=date(2026, 10, 1),
            period_end=date(2026, 12, 31),
            club_fee_kobo=amount,
            minimum_entry_sessions=5,
        ),
        date(2026, 10, 3),
        count=count,
    )


def test_no_schedule_is_not_an_invented_calendar_price():
    plan = quarter()
    plan.session_links = []
    assert schedule.actual_plan_price(plan, on_date=date(2026, 10, 1))[:3] == (
        0,
        0,
        False,
    )


@pytest.mark.parametrize(
    "count,amount", [(11, 5_500_000), (12, 6_000_000), (13, 6_500_000)]
)
def test_actual_included_count_controls_price_not_a_universal_twelve(count, amount):
    assert schedule.actual_plan_price(
        quarter(count, amount), on_date=date(2026, 10, 1)
    ) == (amount, count, True, None)


def test_cancelled_or_replaced_midquarter_swim_is_not_sold():
    plan = quarter()
    plan._actual_session_rows[str(plan.session_links[9].session_id)]["status"] = (
        "cancelled"
    )
    assert schedule.actual_plan_price(plan, on_date=date(2026, 10, 1)) == (
        6_000_000,
        12,
        True,
        None,
    )
    assert plan.club_fee_kobo == 6_500_000  # Published promise/history is untouched.


def test_parallel_experience_does_not_remove_any_swim():
    assert schedule.actual_plan_price(quarter(), on_date=date(2026, 10, 1))[:2] == (
        6_500_000,
        13,
    )


def test_cost_weighted_proration_preserves_override_and_different_venues():
    plan = quarter(5, 2_800_000)
    plan.minimum_entry_sessions = 1
    plan.session_links[-1].fee_kobo = 800_000
    # Four 500k swims + one 800k swim; final explicitly discounted 2.8m.
    assert schedule.actual_plan_price(plan, on_date=date(2026, 10, 31))[:2] == (
        800_000,
        1,
    )
    plan._actual_session_rows[str(plan.session_links[-1].session_id)]["fee_kobo"] = (
        1_000_000
    )
    assert schedule.actual_plan_price(plan, on_date=date(2026, 10, 31))[0] == 800_000


def test_midquarter_uses_real_start_not_weekday_or_calendar_count():
    plan = quarter()
    link = plan.session_links[0]
    row = plan._actual_session_rows[str(link.session_id)]
    row["starts_at"] = "2026-10-07T09:00:00+01:00"  # Wednesday reschedule.
    assert len(schedule.remaining_links(plan, on_date=date(2026, 10, 5))) == 13
    row["pool_id"] = str(uuid4())  # Unreviewed venue change cannot grant coverage.
    assert len(schedule.remaining_links(plan, on_date=date(2026, 10, 5))) == 12


def test_quarter_draft_dates_and_december_wrapup():
    assert next_quarter(date(2026, 9, 30)) == (date(2026, 10, 1), date(2026, 12, 31))
    assert next_quarter(date(2026, 12, 31)) == (date(2027, 1, 1), date(2027, 3, 31))
    assert suggested_experience_day(date(2026, 12, 31)) == date(2026, 12, 5)
    assert suggested_experience_day(date(2026, 9, 30)) == date(2026, 9, 26)


@pytest.mark.asyncio
async def test_publish_requires_re_review_after_session_price_edit(monkeypatch):
    plan = quarter()
    plan.club_id = uuid4()
    plan.id = uuid4()
    plan.published_at = None
    plan._actual_session_rows[str(plan.session_links[0].session_id)]["fee_kobo"] += 1
    monkeypatch.setattr(club_plan_admin, "_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(club_plan_admin, "hydrate_schedules", AsyncMock())
    post = AsyncMock()
    monkeypatch.setattr(club_plan_admin, "internal_post", post)
    with pytest.raises(HTTPException, match="changed"):
        await publish_draft(
            plan.id,
            db=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(is_active=True))
            ),
        )
    post.assert_not_called()


@pytest.mark.asyncio
async def test_exact_link_allows_reviewed_alternate_pool_but_not_extra_swims():
    member_id, club_id, pool, alternate, included, extra = [uuid4() for _ in range(6)]
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    enrollment = SimpleNamespace(
        id=uuid4(),
        member_id=member_id,
        club_id=club_id,
        pool_id=pool,
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=90),
        payment_mode="quarterly_prepaid",
    )
    plan = SimpleNamespace(
        pool_id=pool,
        session_links=[SimpleNamespace(session_id=included, pool_id=alternate)],
    )
    club = SimpleNamespace(default_pool_id=pool)
    membership = SimpleNamespace(
        member_id=member_id,
        post_academy_club_until=None,
        club_paid_until=now + timedelta(days=90),
    )
    checks = [
        SimpleNamespace(
            context_key=str(id),
            session_id=id,
            member_id=member_id,
            at=now,
            pool_id=alternate,
            pod_id=None,
        )
        for id in (included, extra)
    ]
    result = await resolve_club_access_checks(
        _AccessDb([membership], [(enrollment, club, plan)]), checks
    )
    assert result[0]["allowed"] and result[0]["fee_amount_kobo"] == 0
    assert not result[1]["allowed"]  # No fallback into an old broad Club tier.
