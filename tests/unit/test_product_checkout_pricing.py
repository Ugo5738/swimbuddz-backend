"""Product discounts are line-scoped; wallet tender is never another discount."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from services.payments_service.models import DiscountType, PaymentPurpose
from services.payments_service.services import checkout_pricing as pricing


def discount(scopes=None, value=10, kind=DiscountType.PERCENTAGE):
    return SimpleNamespace(
        applies_to=scopes,
        value=value,
        discount_type=kind,
        valid_from=None,
        valid_until=None,
        max_uses=None,
        current_uses=0,
    )


@pytest.mark.parametrize(
    "scopes,expected",
    [
        (["CLUB"], {"club": 650000}),
        (["COMMUNITY"], {"community": 200000}),
        (["CLUB_BUNDLE"], {"club": 650000, "community": 200000}),
        ([], {"club": 650000, "community": 200000}),
        (["COMMUNITY_EXPERIENCE_BUNDLE"], {"community_experience_bundle": 300000}),
    ],
)
def test_mixed_cart_discount_does_not_leak_to_other_items(scopes, expected):
    assert (
        pricing.discount_allocation(
            discount(scopes),
            {
                "club": 6500000,
                "community": 2000000,
                "community_experience_bundle": 3000000,
            },
        )
        == expected
    )


def test_standard_experience_scope_cannot_stack_on_bundle():
    with pytest.raises(HTTPException):
        pricing.discount_allocation(
            discount(["COMMUNITY_EXPERIENCE"]), {"community_experience_bundle": 3000000}
        )


def test_transition_standard_experience_can_receive_standard_discount():
    components = pricing.product_components(
        PaymentPurpose.CLUB,
        7000000,
        {
            "components_kobo": {
                "club": 0,
                "annual_swimbuddz_membership": 2000000,
                "community_experience": 5000000,
            },
            "community_experience_option": {"price_context": "standard_member"},
        },
    )
    assert pricing.discount_allocation(
        discount(["COMMUNITY_EXPERIENCE"]), components
    ) == {"community_experience": 500000}


def test_fixed_code_is_capped_and_allocated_exactly():
    values = pricing.discount_allocation(
        discount([], 0.01, DiscountType.FIXED),
        {"community": 101, "academy_cohort": 101},
    )
    assert sum(values.values()) == 1
    assert values == {"community": 0, "academy_cohort": 1}
    assert pricing.discount_allocation(
        discount(["CLUB"], 90000, DiscountType.FIXED), {"club": 6500000}
    ) == {"club": 6500000}


@pytest.mark.parametrize(
    "metadata",
    [
        {"components_kobo": {"club": -1}},
        {"checkout_components_kobo": {"community": 100}},
    ],
)
def test_component_mismatch_fails_closed(metadata):
    with pytest.raises(HTTPException, match="Checkout components"):
        pricing.product_components(PaymentPurpose.CLUB, 200, metadata)


@pytest.fixture
def db():
    return SimpleNamespace(execute=AsyncMock(return_value=MagicMock()), add=MagicMock())


@pytest.mark.asyncio
async def test_discount_then_bubbles_then_fees_on_cash_only(db, monkeypatch):
    promo = discount(["ACADEMY_COHORT"])
    db.execute.return_value.scalar_one_or_none.return_value = promo
    charges = AsyncMock(
        return_value=([{"label": "Processing", "amount_kobo": 10000}], 10000)
    )
    monkeypatch.setattr(pricing, "calculate_additional_charges", charges)
    quote = await pricing.price_product_checkout(
        db,
        purpose=PaymentPurpose.ACADEMY_COHORT,
        currency="NGN",
        components={"academy_cohort": 24000000, "community": 2000000},
        discount_code="learn",
        bubbles_to_apply=100,
        consume_discount=True,
    )
    assert quote["discount_kobo"] == 2400000
    assert quote["net_components_kobo"]["community"] == 2000000
    assert quote["total_kobo"] == 22610000
    assert quote["bubbles_value_kobo"] == 1000000
    assert charges.await_args.kwargs["subtotal_kobo"] == 22600000
    assert promo.current_uses == 1


@pytest.mark.parametrize(
    "components,bubbles", [({"club": 0}, 0), ({"community": 2000000}, 200)]
)
@pytest.mark.asyncio
async def test_free_or_all_bubbles_never_charges_provider_fee(
    db, monkeypatch, components, bubbles
):
    charges = AsyncMock()
    monkeypatch.setattr(pricing, "calculate_additional_charges", charges)
    quote = await pricing.price_product_checkout(
        db,
        purpose=PaymentPurpose.CLUB,
        currency="NGN",
        components=components,
        bubbles_to_apply=bubbles,
    )
    assert quote["total_kobo"] == 0
    charges.assert_not_awaited()


@pytest.mark.parametrize("method,bubbles", [("manual_transfer", 1), ("paystack", 201)])
@pytest.mark.asyncio
async def test_invalid_wallet_tender_rejected(db, method, bubbles):
    with pytest.raises(HTTPException):
        await pricing.price_product_checkout(
            db,
            purpose=PaymentPurpose.COMMUNITY,
            currency="NGN",
            components={"community": 2000000},
            payment_method=method,
            bubbles_to_apply=bubbles,
        )


@pytest.mark.asyncio
async def test_preview_does_not_consume_code_and_exhausted_code_rejected(
    db, monkeypatch
):
    promo = discount(["COMMUNITY"])
    db.execute.return_value.scalar_one_or_none.return_value = promo
    monkeypatch.setattr(
        pricing, "calculate_additional_charges", AsyncMock(return_value=([], 0))
    )
    await pricing.price_product_checkout(
        db,
        purpose=PaymentPurpose.COMMUNITY,
        currency="NGN",
        components={"community": 2000000},
        discount_code="member",
    )
    assert promo.current_uses == 0
    db.add.assert_not_called()
    promo.max_uses = 0
    with pytest.raises(HTTPException, match="usage limit"):
        await pricing.price_product_checkout(
            db,
            purpose=PaymentPurpose.COMMUNITY,
            currency="NGN",
            components={"community": 2000000},
            discount_code="member",
        )


def test_reviewed_amount_must_still_match():
    pricing.verify_expected_total({"total_kobo": 100}, 100)
    with pytest.raises(HTTPException, match="price changed"):
        pricing.verify_expected_total({"total_kobo": 101}, 100)
