"""Payment initialization cannot escape before Sessions guarantees capacity."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment
from services.payments_service.routers.intents import intent_creation
from services.payments_service.schemas import CreatePaymentIntentRequest
from services.payments_service.services import club_checkout_capacity

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def checkout(monkeypatch):
    user = AuthUser(user_id=str(uuid4()), email="swimmer@example.com")
    plan = str(uuid4())
    payload = CreatePaymentIntentRequest(
        purpose="club", club_application_id=uuid4(), idempotency_key=str(uuid4())
    )
    monkeypatch.setattr(
        intent_creation,
        "_approved_club_application_context",
        AsyncMock(
            return_value={
                "member_auth_id": user.user_id,
                "currency": "NGN",
                "subtotal_kobo": 520000,
                "payment_mode": "quarterly_prepaid",
                "club_id": str(uuid4()),
                "club_name": "Selected Club",
                "plan_version_id": plan,
                "community_experience_selected": False,
                "club_fee_kobo": 520000,
                "community_experience_fee_kobo": 0,
            }
        ),
    )
    monkeypatch.setattr(intent_creation, "_paystack_enabled", lambda: True)
    monkeypatch.setattr(
        intent_creation, "_set_pending_tier_payment_for_payment", AsyncMock()
    )
    steps = []

    async def post(**kwargs):
        protection = kwargs["path"].endswith("/protect")
        steps.append("protect" if protection else "reserve")
        return httpx.Response(
            200,
            json={
                "session_holds": True,
                "status": "protected" if protection else "active",
                "plan_version_ids": [plan],
                "expires_at": (utc_now() + timedelta(minutes=30)).isoformat(),
            },
        )

    monkeypatch.setattr(intent_creation, "internal_post", post)
    monkeypatch.setattr(club_checkout_capacity, "internal_post", post)

    async def initialize(payment, *_args):
        assert steps == ["reserve", "protect"]
        assert payment.payment_metadata["club_capacity_reservation"]["protected_at"]
        steps.append("provider")
        return "https://checkout.example.com/quarter", "code"

    provider = AsyncMock(side_effect=initialize)
    monkeypatch.setattr(intent_creation, "_initialize_paystack", provider)
    return user, payload, steps, provider


@pytest.mark.parametrize("method", ["paystack", "manual_transfer"])
async def test_all_seats_protected_before_payable_link(db_session, checkout, method):
    user, payload, steps, provider = checkout
    result = await intent_creation.create_payment_intent(
        payload.model_copy(update={"payment_method": method}), user, db_session
    )
    assert result.checkout_url
    assert steps == (
        ["reserve", "protect", "provider"]
        if method == "paystack"
        else ["reserve", "protect"]
    )
    if method == "manual_transfer":
        provider.assert_not_awaited()


async def test_capacity_conflict_prevents_payment_creation(
    db_session, checkout, monkeypatch
):
    user, payload, _steps, provider = checkout
    monkeypatch.setattr(
        intent_creation,
        "_reserve_club_application_capacity",
        AsyncMock(side_effect=HTTPException(409, "Included swim is full")),
    )
    with pytest.raises(HTTPException, match="full"):
        await intent_creation.create_payment_intent(payload, user, db_session)
    provider.assert_not_awaited()
    assert not await db_session.scalar(
        select(Payment.id).where(Payment.member_auth_id == user.user_id)
    )


async def test_protection_failure_never_initializes_provider(
    db_session, checkout, monkeypatch
):
    user, payload, steps, provider = checkout
    monkeypatch.setattr(
        club_checkout_capacity,
        "internal_post",
        AsyncMock(
            return_value=httpx.Response(
                409, json={"detail": "Seat could not be protected"}
            )
        ),
    )
    with pytest.raises(HTTPException, match="protected"):
        await intent_creation.create_payment_intent(payload, user, db_session)
    assert steps == ["reserve"]
    provider.assert_not_awaited()
