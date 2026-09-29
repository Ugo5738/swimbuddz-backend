from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from libs.auth.models import AuthUser
from services.sessions_service.schemas.template_admission import (
    TemplateAdmissionSettings,
)
from services.sessions_service.services.template_operations import template_admission
from services.members_service.services.club_reservations import reserve_enrollment_swims
from services.payments_service.routers.intents import admin
from services.payments_service.services.product_intent_retry import request_fingerprint
from services.payments_service.schemas import CreatePaymentIntentRequest


def test_template_admission_requires_independent_explicit_prices():
    with pytest.raises(ValidationError):
        TemplateAdmissionSettings(guest_booking_mode="public")
    with pytest.raises(ValidationError):
        TemplateAdmissionSettings(allows_community_dropins=True)
    starts = datetime.now(timezone.utc)
    values = template_admission(
        SimpleNamespace(
            admission_settings={
                "guest_fee": 0,
                "guest_booking_mode": "public",
                "community_dropin_fee": 7000,
                "allows_community_dropins": True,
                "guest_booking_cutoff_hours": 3,
            }
        ),
        starts,
    )
    assert (
        values["guest_fee_kobo"] == 0 and values["community_dropin_fee_kobo"] == 700000
    )
    assert values["guest_booking_closes_at"] == starts - timedelta(hours=3)


@pytest.mark.asyncio
async def test_transition_enrollment_never_auto_books(monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(
        "services.members_service.services.club_reservations.internal_post", post
    )
    await reserve_enrollment_swims(
        SimpleNamespace(payment_mode="transition_per_session"), None, None
    )
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["test@example.com", None])
async def test_admin_link_creates_no_payment_or_provider_transaction(
    monkeypatch, email
):
    booking_id = uuid4()
    monkeypatch.setattr(
        admin,
        "get_booking_by_id",
        AsyncMock(
            return_value={
                "status": "confirmed",
                "fee_amount_kobo": 520000,
                "member_id": str(uuid4()),
                "member_auth_id": str(uuid4()),
                "session_id": str(uuid4()),
            }
        ),
    )
    monkeypatch.setattr(
        admin, "get_member_by_id", AsyncMock(return_value={"email": email})
    )
    monkeypatch.setattr(
        admin, "_find_paid_booking_payment", AsyncMock(return_value=None)
    )
    db = SimpleNamespace(add=AsyncMock(), commit=AsyncMock())
    result = await admin.admin_generate_booking_pay_link(
        booking_id, admin.AdminBookingPayLinkRequest(), AuthUser(sub=str(uuid4())), db
    )
    assert result.authorization_url.endswith(f"/account/billing/sessions/{booking_id}")
    assert result.reference is None and result.amount == 5200
    assert result.payer_email == email
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def test_settlement_idempotency_binds_booking_identity():
    body = {
        "purpose": "session_booking",
        "currency": "NGN",
        "direct_amount": 5200,
        "idempotency_key": str(uuid4()),
        "payment_metadata": {"booking_id": str(uuid4())},
    }
    one = CreatePaymentIntentRequest(**body)
    two = one.model_copy(update={"payment_metadata": {"booking_id": str(uuid4())}})
    assert request_fingerprint(one) != request_fingerprint(two)


@pytest.mark.asyncio
async def test_next_quarter_retains_all_selected_series(monkeypatch):
    from datetime import date
    from services.members_service.routers import club_plan_admin

    template_ids = [uuid4(), uuid4()]
    source = SimpleNamespace(
        id=uuid4(),
        club_id=uuid4(),
        period_end=date(2026, 12, 31),
        session_links=[SimpleNamespace(session_id=uuid4()) for _ in range(3)],
        capacity=20,
        minimum_entry_sessions=5,
        refreshments_included=True,
    )
    monkeypatch.setattr(club_plan_admin, "_plan", AsyncMock(return_value=source))
    monkeypatch.setattr(
        club_plan_admin,
        "fetch_schedule",
        AsyncMock(
            return_value=[
                {"template_id": str(template_ids[0])},
                {"template_id": str(template_ids[1])},
                {"template_id": str(template_ids[0])},
            ]
        ),
    )
    recommend = AsyncMock(return_value={"draft": True})
    monkeypatch.setattr(club_plan_admin, "recommend_quarter", recommend)
    await club_plan_admin.generate_next_quarter(
        source.id,
        club_plan_admin.NextQuarterRequest(),
        AuthUser(sub=str(uuid4())),
        object(),
    )
    request = recommend.call_args.args[0]
    assert request.template_ids == sorted(template_ids) and request.template_id is None
    assert (request.year, request.quarter) == (2027, 1)


def test_quarter_selection_rejects_ambiguous_or_duplicate_series():
    from services.members_service.routers.club_plan_admin import NextQuarterRequest

    template_id = uuid4()
    with pytest.raises(ValidationError):
        NextQuarterRequest(template_id=template_id, template_ids=[uuid4()])
    with pytest.raises(ValidationError):
        NextQuarterRequest(template_ids=[template_id, template_id])
