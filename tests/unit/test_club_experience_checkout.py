from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.members_service.routers import clubs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,linked,expected_experience",
    [
        ("quarterly_prepaid", False, 0),
        ("quarterly_prepaid", True, 3_000_000),
        ("transition_per_session", False, 0),
        ("transition_per_session", True, 0),
    ],
)
async def test_checkout_only_charges_a_fulfillable_quarterly_experience(
    monkeypatch,
    mode,
    linked,
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
        is_active=True,
        purchase_opens_at=None,
        purchase_closes_at=None,
        club_bundle_fee_kobo=3_000_000,
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
        community_experience_selected=True,
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
                SimpleNamespace(scalar_one_or_none=lambda: None),
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
        _service=None,
        db=db,
    )
    assert quote.community_experience_fee_kobo == expected_experience
    assert quote.community_experience_selected is bool(expected_experience)
    expected_club = 0 if mode == "transition_per_session" else 6_500_000
    assert quote.club_fee_kobo == expected_club
    assert quote.annual_membership_fee_kobo == 2_000_000
    assert quote.subtotal_kobo == expected_club + 2_000_000 + expected_experience
