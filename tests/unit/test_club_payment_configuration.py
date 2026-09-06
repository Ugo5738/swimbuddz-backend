from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from services.payments_service.models import PaymentPurpose
from services.members_service.routers._club_pricing import plan_price
from services.members_service.schemas.club import CommunityExperienceOfferingCreate
from services.payments_service.services.additional_charges import (
    calculate_additional_charges,
)
from services.members_service.schemas.member import ActivateClubRequest
from services.sessions_service.schemas.guest_pass import GuestPassCreate
from services.sessions_service.schemas.main import SessionCreate, SessionType
from services.sessions_service.routers import guest_passes
from scripts.seed.reward_rules import build_default_rules


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


def test_thirteen_session_quarter_is_not_capped_at_twelve():
    plan = SimpleNamespace(
        period_start=date(2026, 10, 1), period_end=date(2026, 12, 31),
        sessions_included=13, club_fee_kobo=6_500_000, minimum_entry_sessions=5,
    )
    club = SimpleNamespace(default_session_day="sat")
    assert plan_price(plan, club, on_date=date(2026, 10, 3)) == (6_500_000, 13, True, None)


@pytest.mark.asyncio
async def test_payment_charge_formula_supports_waiver_and_cap():
    policy = SimpleNamespace(
        id="policy-1",
        label="Online payment processing",
        calculation_mode="additive",
        rate_basis_points=150,
        fixed_amount_kobo=10_000,
        waive_fixed_below_kobo=250_000,
        cap_amount_kobo=200_000,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Scalars([policy])))

    lines, total = await calculate_additional_charges(
        db,
        purpose=PaymentPurpose.GUEST_PASS,
        payment_method="paystack",
        subtotal_kobo=700_000,
    )

    assert total == 20_500
    assert lines == [
        {
            "policy_id": "policy-1",
            "label": "Online payment processing",
            "calculation_mode": "additive",
            "amount_kobo": 20_500,
            "rate_basis_points": 150,
            "fixed_amount_kobo": 10_000,
        }
    ]


@pytest.mark.asyncio
async def test_payment_charge_waives_fixed_part_below_threshold():
    policy = SimpleNamespace(
        id="policy-1",
        label="Online payment processing",
        calculation_mode="additive",
        rate_basis_points=150,
        fixed_amount_kobo=10_000,
        waive_fixed_below_kobo=250_000,
        cap_amount_kobo=200_000,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Scalars([policy])))

    lines, total = await calculate_additional_charges(
        db,
        purpose=PaymentPurpose.SESSION_BOOKING,
        payment_method="paystack",
        subtotal_kobo=200_000,
    )

    assert total == 3_000
    assert lines[0]["fixed_amount_kobo"] == 0


def test_minor_guest_requires_guardian_details():
    with pytest.raises(ValidationError, match="Guardian name and phone"):
        GuestPassCreate(
            full_name="Young Swimmer",
            email="young@example.com",
            phone="08000000000",
            date_of_birth=date(date.today().year - 12, 1, 1),
            waiver_accepted=True,
        )


def test_marketing_consent_is_separate_and_off_by_default():
    guest = GuestPassCreate(
        full_name="Adult Swimmer",
        email="adult@example.com",
        phone="08000000000",
        waiver_accepted=True,
    )

    assert guest.marketing_consent is False


def test_guest_phone_is_normalized_for_repeat_guest_deduplication():
    assert guest_passes._normalize_guest_phone("0801 234-5678") == "+2348012345678"
    assert guest_passes._normalize_guest_phone("+234 801 234 5678") == "+2348012345678"


def test_guest_and_community_prices_are_configured_separately():
    session = SessionCreate(
        title="Saturday Club",
        session_type=SessionType.CLUB,
        starts_at="2026-08-15T09:00:00+01:00",
        ends_at="2026-08-15T12:00:00+01:00",
        guest_fee=7000,
        community_dropin_fee=7000,
    )

    assert session.guest_fee == 7000
    assert session.community_dropin_fee == 7000


@pytest.mark.asyncio
async def test_payment_charge_can_gross_up_provider_fee():
    policy = SimpleNamespace(
        id="policy-1",
        label="Online payment processing",
        calculation_mode="gross_up",
        rate_basis_points=150,
        fixed_amount_kobo=10_000,
        waive_fixed_below_kobo=250_000,
        cap_amount_kobo=200_000,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_Scalars([policy])))

    lines, total = await calculate_additional_charges(
        db,
        purpose=PaymentPurpose.GUEST_PASS,
        payment_method="paystack",
        subtotal_kobo=700_000,
    )

    assert total == 20_813
    assert lines[0]["calculation_mode"] == "gross_up"


def test_location_club_activation_can_preserve_separate_community_expiry():
    activation = ActivateClubRequest(months=3, extend_community_membership=False)

    assert activation.skip_community_check is False
    assert activation.extend_community_membership is False


@pytest.mark.asyncio
async def test_guest_referral_code_resolves_to_the_referrer(monkeypatch):
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"referrer_auth_id": "peter-auth-id"},
    )
    resolve = AsyncMock(return_value=response)
    monkeypatch.setattr(guest_passes, "internal_get", resolve)

    result = await guest_passes._resolve_referrer_auth_id("SB-PETER-1234")

    assert result == "peter-auth-id"
    assert resolve.await_args.kwargs["params"] == {"code": "SB-PETER-1234"}


def test_club_quarter_prorates_until_five_session_cutoff():
    plan = SimpleNamespace(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        sessions_included=13,
        minimum_entry_sessions=5,
        club_fee_kobo=6_000_000,
    )
    club = SimpleNamespace(default_session_day="sat")

    amount, remaining, available, reason = plan_price(
        plan, club, on_date=date(2026, 8, 25)
    )
    assert (amount, remaining, available, reason) == (2_307_693, 5, True, None)

    amount, remaining, available, reason = plan_price(
        plan, club, on_date=date(2026, 9, 1)
    )
    assert (amount, remaining, available) == (0, 4, False)
    assert "Community drop-ins" in reason


def test_future_club_quarter_is_full_price():
    plan = SimpleNamespace(
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        sessions_included=13,
        minimum_entry_sessions=5,
        club_fee_kobo=6_500_000,
    )
    club = SimpleNamespace(default_session_day="sat")

    assert plan_price(plan, club, on_date=date(2026, 8, 25)) == (
        6_500_000,
        13,
        True,
        None,
    )


def test_community_experience_price_ladder_is_enforced():
    with pytest.raises(ValidationError, match="bundle <= Club later <= standard"):
        CommunityExperienceOfferingCreate(
            name="Q4 Experience",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 12, 31),
            standard_member_fee_kobo=5_000_000,
            club_member_fee_kobo=2_000_000,
            club_bundle_fee_kobo=3_000_000,
        )


def test_guest_first_attendance_reward_is_ten_bubbles():
    rule = next(
        item
        for item in build_default_rules()
        if item.event_type == "referral.guest_attended"
    )

    assert rule.reward_bubbles == 10
    assert rule.rule_name == "guest_first_attendance_referral"
