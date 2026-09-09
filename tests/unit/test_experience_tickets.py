from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.members_service.routers import experience_tickets as tickets
from services.members_service.routers import experience_admin as admin
from services.members_service.schemas.experience import (
    ExperienceOrderCreate,
    ExperienceParticipantInput,
    ExperienceOrderConfirm,
    ExperienceEventsUpdate,
)
from services.members_service.services import experience_events, experience_ticketing
from services.payments_service.routers.intents._entitlement import _community_experience


def details(name="AY Test"):
    return {
        "full_name": name,
        "email": "test@example.com",
        "phone": "08012345678",
        "emergency_contact_name": "Contact",
        "emergency_contact_phone": "08098765432",
        "waiver_accepted": True,
    }


def body(**changes):
    return ExperienceOrderCreate(
        **{
            "idempotency_key": uuid4(),
            "access_token": "secret" * 8,
            "participant": details(),
            **changes,
        }
    )


def offering():
    return SimpleNamespace(
        id=uuid4(),
        name="Weekend trip",
        currency="NGN",
        is_active=True,
        period_start=date(2027, 1, 1),
        period_end=date(2027, 3, 31),
        purchase_opens_at=None,
        purchase_closes_at=None,
        member_guest_fee_kobo=5_500_000,
        public_guest_fee_kobo=6_000_000,
        max_guests_per_member=2,
        capacity=50,
        standard_member_fee_kobo=5_000_000,
        club_member_fee_kobo=4_000_000,
        club_bundle_fee_kobo=3_000_000,
    )


@pytest.fixture
def checkout(monkeypatch):
    product = offering()
    member = SimpleNamespace(id=uuid4(), auth_id="member-auth")
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one_or_none=lambda: None, first=lambda: None
            )
        ),
        commit=AsyncMock(),
        get=AsyncMock(),
    )
    orders = []

    def add(order):
        order.id, order.created_at = uuid4(), datetime.now(timezone.utc)
        for person in order.participants:
            person.id = uuid4()
        orders.append(order)

    db.add = Mock(side_effect=add)
    monkeypatch.setattr(tickets, "lock_offering", AsyncMock(return_value=product))
    monkeypatch.setattr(tickets, "_member_by_auth", AsyncMock(return_value=member))
    monkeypatch.setattr(
        tickets,
        "live_events",
        AsyncMock(return_value=[{"visibility": "public", "max_capacity": 40}]),
    )
    capacity = AsyncMock()
    monkeypatch.setattr(tickets, "assert_capacity", capacity)
    monkeypatch.setattr(tickets, "existing_member_guests", AsyncMock(return_value=0))
    quote = SimpleNamespace(
        already_purchased=False,
        amount_kobo=4_000_000,
        price_context="club_member_later",
        annual_membership_fee_kobo=2_000_000,
        annual_membership_months=12,
    )
    monkeypatch.setattr(tickets, "_quote_for_member", AsyncMock(return_value=quote))
    return SimpleNamespace(
        db=db,
        product=product,
        member=member,
        quote=quote,
        orders=orders,
        capacity=capacity,
    )


@pytest.mark.asyncio
async def test_member_can_pay_for_two_named_guests_at_server_prices(checkout):
    c = checkout
    result = await tickets.create_order(
        c.product.id,
        body(guests=[details("Guest One"), details("Guest Two")]),
        current_user=SimpleNamespace(user_id="member-auth"),
        db=c.db,
    )
    assert result.amount_kobo == 4_000_000 + 2_000_000 + 2 * 5_500_000
    assert [ticket["ticket_kind"] for ticket in result.tickets] == [
        "club_member_later",
        "member_guest",
        "member_guest",
    ]
    assert result.participant_count == 3
    assert all(person.waiver_accepted_at for person in c.orders[0].participants)
    assert c.capacity.await_args.args[-2:] == (40, 3)
    c.product.member_guest_fee_kobo = 99_000_000
    assert (
        c.orders[0].participants[1].price_kobo == 5_500_000
    )  # Purchased terms stay frozen.


@pytest.mark.asyncio
async def test_public_guest_never_acquires_or_pays_annual_membership(checkout):
    c = checkout
    response = await tickets.create_order(
        c.product.id, body(), current_user=None, db=c.db
    )
    assert response.amount_kobo == 6_000_000
    assert response.membership_fee_kobo == 0
    assert c.orders[0].member_id is None and c.orders[0].membership_months == 0
    tickets._quote_for_member.assert_not_called()


@pytest.mark.asyncio
async def test_guests_only_order_does_not_charge_member_twice(checkout):
    c = checkout
    c.quote.already_purchased = True
    result = await tickets.create_order(
        c.product.id,
        body(include_member=False, guests=[details("Plus One")]),
        current_user=SimpleNamespace(user_id="member-auth"),
        db=c.db,
    )
    assert result.amount_kobo == 5_500_000 and result.membership_fee_kobo == 0
    assert c.orders[0].participants[0].member_id is None


@pytest.mark.asyncio
async def test_guest_limit_includes_existing_party(checkout, monkeypatch):
    c = checkout
    monkeypatch.setattr(tickets, "existing_member_guests", AsyncMock(return_value=2))
    with pytest.raises(HTTPException, match="per-member limit"):
        await tickets.create_order(
            c.product.id,
            body(guests=[details()]),
            current_user=SimpleNamespace(user_id="member-auth"),
            db=c.db,
        )
    c.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_public_guest_cannot_buy_members_only_event(checkout, monkeypatch):
    c = checkout
    monkeypatch.setattr(
        tickets, "live_events", AsyncMock(return_value=[{"visibility": "members_only"}])
    )
    with pytest.raises(HTTPException, match="not open to public"):
        await tickets.create_order(c.product.id, body(), current_user=None, db=c.db)


@pytest.mark.asyncio
async def test_order_retry_returns_frozen_original_without_another_hold(checkout):
    c = checkout
    request = body()
    original = await tickets.create_order(
        c.product.id, request, current_user=None, db=c.db
    )
    c.db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: c.orders[0])
    )
    c.product.public_guest_fee_kobo *= 2
    repeated = await tickets.create_order(
        c.product.id, request, current_user=None, db=c.db
    )
    assert repeated.id == original.id and repeated.amount_kobo == original.amount_kobo
    assert c.db.add.call_count == 1 and c.capacity.await_count == 1


@pytest.mark.parametrize(
    "changes", [{"amount_kobo": 1}, {"price_context": "club_bundle"}]
)
def test_client_cannot_inject_price_or_bundle_eligibility(changes):
    with pytest.raises(ValidationError):
        body(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"waiver_accepted": False},
        {"full_name": "   "},
        {"emergency_contact_phone": " "},
    ],
)
def test_safety_consent_and_contacts_cannot_be_empty(changes):
    with pytest.raises(ValidationError):
        ExperienceParticipantInput(**{**details(), **changes})


def test_public_event_redaction_does_not_leak_supplier_costs_or_private_venue():
    public = experience_events.public_event(
        {
            "title": "Trip",
            "location": "private address",
            "is_location_private": True,
            "cost_lines": [{"amount": 123}],
            "recommended_price_kobo": 999,
        }
    )
    assert (
        public["location"] is None
        and "cost_lines" not in public
        and "recommended_price_kobo" not in public
    )


def test_order_access_secret_is_hashed_and_not_public():
    order = SimpleNamespace(
        access_token_hash=experience_ticketing.token_hash("correct secret")
    )
    experience_ticketing.verify_order_token(order, "correct secret")
    with pytest.raises(HTTPException):
        experience_ticketing.verify_order_token(order, "incorrect secret")


def test_replacement_requires_explicit_affected_sessions():
    with pytest.raises(ValidationError):
        ExperienceEventsUpdate(
            events=[{"event_id": uuid4(), "club_impact": "replaces"}]
        )
    with pytest.raises(ValidationError):
        ExperienceEventsUpdate(
            events=[
                {
                    "event_id": uuid4(),
                    "club_impact": "parallel",
                    "replaced_session_ids": [uuid4()],
                }
            ]
        )


@pytest.mark.asyncio
async def test_capacity_counts_live_holds_and_legacy_purchases():
    statements = []

    async def execute(statement):
        statements.append(statement)
        return SimpleNamespace(scalar_one=lambda: 2)

    with pytest.raises(HTTPException, match="enough places"):
        await experience_ticketing.assert_capacity(
            SimpleNamespace(execute=execute), uuid4(), 4, 1
        )
    sql = " ".join(str(statement) for statement in statements)
    assert (
        "expires_at" in sql
        and "community_experience_purchases" in sql
        and "NOT IN" in sql
    )


@pytest.mark.asyncio
async def test_fulfillment_rejects_wrong_amount_and_replays_confirmation(monkeypatch):
    order = SimpleNamespace(
        id=uuid4(),
        offering_id=uuid4(),
        amount_kobo=7_000_000,
        payment_reference="EXP-PAID",
        status="confirmed",
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=order),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: order)),
    )
    monkeypatch.setattr(tickets, "lock_offering", AsyncMock(return_value=offering()))
    with pytest.raises(HTTPException, match="does not match"):
        await tickets.confirm_order(
            order.id,
            ExperienceOrderConfirm(payment_reference="EXP-PAID", amount_kobo=1),
            db=db,
        )
    assert await tickets.confirm_order(
        order.id,
        ExperienceOrderConfirm(payment_reference="EXP-PAID", amount_kobo=7_000_000),
        db=db,
    ) == {"confirmed": True}


@pytest.mark.asyncio
async def test_payment_fulfillment_checks_paid_amount_before_contacting_members(
    monkeypatch,
):
    post = AsyncMock(return_value=httpx.Response(200, json={"confirmed": True}))
    monkeypatch.setattr("libs.common.service_client.internal_post", post)
    payment = SimpleNamespace(
        amount=10,
        payment_metadata={
            "experience_order_id": str(uuid4()),
            "experience_order_amount_kobo": 6_000_000,
        },
        reference="wrong-amount",
    )
    with pytest.raises(HTTPException, match="Paid amount"):
        await _community_experience.apply_community_experience(payment)
    post.assert_not_called()


@pytest.mark.asyncio
async def test_expired_checkout_does_not_initialize_another_payment(monkeypatch):
    order = SimpleNamespace(
        status="pending_payment",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    monkeypatch.setattr(tickets, "_order_access", AsyncMock(return_value=order))
    post = AsyncMock()
    monkeypatch.setattr(tickets, "internal_post", post)
    with pytest.raises(HTTPException, match="expired"):
        await tickets.order_checkout(
            uuid4(), SimpleNamespace(access_token="token"), db=Mock()
        )
    post.assert_not_called()


@pytest.mark.asyncio
async def test_checkin_rejects_missing_bundle_waiver():
    participant = SimpleNamespace(order_id=uuid4(), waiver_accepted_at=None)
    order = SimpleNamespace(status="confirmed")
    db = SimpleNamespace(get=AsyncMock(side_effect=[participant, order]))
    with pytest.raises(HTTPException, match="waiver"):
        await admin.check_in(
            uuid4(),
            SimpleNamespace(event_id=uuid4()),
            admin=SimpleNamespace(user_id="admin"),
            db=db,
        )
