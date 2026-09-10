from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.members_service.routers import clubs
from tests.club_schedule_helpers import attach_schedule
from services.members_service.services import club_plan_schedule, experience_events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,linked,selected,choice,available,expected_experience",
    [
        ("quarterly_prepaid", False, True, None, True, 0),
        ("quarterly_prepaid", True, True, None, True, 3_000_000),
        ("quarterly_prepaid", True, True, False, True, 0),
        ("transition_per_session", False, True, None, True, 0),
        ("transition_per_session", True, True, None, True, 5_000_000),
        ("transition_per_session", True, True, False, True, 0),
        ("transition_per_session", True, False, None, True, 0),
        ("transition_per_session", True, False, True, True, 5_000_000),
        ("transition_per_session", True, True, True, False, 0),
    ],
)
@pytest.mark.parametrize("membership_covered", [False, True])
async def test_checkout_only_charges_the_selected_fulfillable_experience(
    monkeypatch,
    mode,
    linked,
    selected,
    choice,
    available,
    membership_covered,
    expected_experience,
):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 6)

    monkeypatch.setattr(clubs, "date", FrozenDate)
    monkeypatch.setattr(
        clubs, "utc_now", lambda: datetime(2026, 9, 6, tzinfo=timezone.utc)
    )
    member = SimpleNamespace(id=uuid4(), auth_id="ay-auth")
    club = SimpleNamespace(id=uuid4(), name="Yaba Club", default_session_day="sat")
    offering = SimpleNamespace(
        id=uuid4(),
        name="Q4 Community Experience",
        currency="NGN",
        period_end=date(2026, 12, 31),
        is_active=available,
        purchase_opens_at=None,
        purchase_closes_at=None,
        club_bundle_fee_kobo=3_000_000,
        club_member_fee_kobo=4_000_000,
        standard_member_fee_kobo=5_000_000,
    )
    plan = SimpleNamespace(
        id=uuid4(),
        name="Q4 Club",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_500_000,
        sessions_included=13,
        minimum_entry_sessions=5,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        community_experience_offering_id=offering.id if linked else None,
        community_experience_fee_kobo=3_000_000,
        community_experience_default_selected=True,
    )
    application = SimpleNamespace(
        id=uuid4(),
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="approved",
        approved_payment_modes=[mode],
        selected_payment_mode=None,
        transition_expires_at=date(2026, 12, 31),
        community_experience_selected=selected,
    )
    attach_schedule(plan, date(2026, 10, 3))
    monkeypatch.setattr(
        club_plan_schedule,
        "fetch_schedule",
        AsyncMock(return_value=list(plan._actual_session_rows.values())),
    )
    monkeypatch.setattr(
        club_plan_schedule, "utc_now", lambda: datetime(2026, 9, 6, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        experience_events, "live_events", AsyncMock(return_value=[{"id": str(uuid4())}])
    )
    records = {
        clubs.ClubApplication: application,
        clubs.Member: member,
        clubs.Club: club,
        clubs.ClubPlanVersion: plan,
        clubs.CommunityExperienceOffering: offering,
    }
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: records[model]),
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    scalar_one_or_none=lambda: SimpleNamespace(
                        community_paid_until=datetime(2027, 1, 1, tzinfo=timezone.utc)
                    )
                    if membership_covered
                    else None
                ),
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(first=lambda: None),
            ]
        ),
    )
    monkeypatch.setattr(clubs, "_assert_plan_capacity", AsyncMock())
    monkeypatch.setattr(clubs, "_assert_pod_capacity", AsyncMock())
    quote = await clubs.get_club_application_payment_context(
        application.id,
        payment_mode=mode,
        community_experience_selected=choice,
        _service=None,
        db=db,
    )
    assert quote.community_experience_fee_kobo == expected_experience
    assert quote.community_experience_selected is bool(expected_experience)
    expected_club = 0 if mode == "transition_per_session" else 6_500_000
    assert quote.club_fee_kobo == expected_club
    membership_fee = 0 if membership_covered else 2_000_000
    assert quote.annual_membership_fee_kobo == membership_fee
    assert quote.subtotal_kobo == expected_club + membership_fee + expected_experience
    if linked and available:
        assert quote.community_experience_option["amount_kobo"] == (
            5_000_000 if mode == "transition_per_session" else 3_000_000
        )
    else:
        assert quote.community_experience_option is None
