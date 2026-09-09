"""PostgreSQL capacity/idempotency regressions. Run in CI, not against production."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from services.members_service.models import CommunityExperienceOffering
from services.members_service.models.experience import (
    CommunityExperienceEvent,
    CommunityExperienceOrder,
    CommunityExperienceParticipant,
)
from services.members_service.routers import experience_tickets as tickets
from services.members_service.schemas.experience import (
    ExperienceOrderConfirm,
    ExperienceOrderCreate,
)


def request_body():
    return ExperienceOrderCreate(
        idempotency_key=uuid4(),
        access_token=uuid4().hex + uuid4().hex,
        participant={
            "full_name": "Guest Participant",
            "email": "guest@example.com",
            "phone": "08012345678",
            "emergency_contact_name": "Emergency Contact",
            "emergency_contact_phone": "08098765432",
            "waiver_accepted": True,
        },
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_named_guest_hold_capacity_frozen_price_and_idempotent_confirmation(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    offering = CommunityExperienceOffering(
        name="Ticket order integration",
        currency="NGN",
        period_start=now.date(),
        period_end=(now + timedelta(days=90)).date(),
        standard_member_fee_kobo=5_000_000,
        club_member_fee_kobo=4_000_000,
        club_bundle_fee_kobo=3_000_000,
        public_guest_fee_kobo=6_000_000,
        member_guest_fee_kobo=5_500_000,
        max_guests_per_member=2,
        capacity=1,
        event_links=[
            CommunityExperienceEvent(
                event_id=uuid4(),
                club_impact="parallel",
                replaced_session_ids=[],
                event_snapshot={},
            )
        ],
    )
    db_session.add(offering)
    await db_session.commit()
    monkeypatch.setattr(
        tickets,
        "live_events",
        AsyncMock(return_value=[{"visibility": "public", "max_capacity": 10}]),
    )
    request = request_body()
    first = await tickets.create_order(
        offering.id, request, current_user=None, db=db_session
    )
    offering.public_guest_fee_kobo = 7_000_000
    await db_session.commit()
    retry = await tickets.create_order(
        offering.id, request, current_user=None, db=db_session
    )
    assert (
        retry.id == first.id
        and retry.amount_kobo == 6_000_000
        and retry.membership_fee_kobo == 0
    )
    with pytest.raises(HTTPException, match="enough places"):
        await tickets.create_order(
            offering.id, request_body(), current_user=None, db=db_session
        )
    paid = ExperienceOrderConfirm(
        payment_reference=first.payment_reference, amount_kobo=first.amount_kobo
    )
    assert await tickets.confirm_order(first.id, paid, db=db_session) == {
        "confirmed": True
    }
    assert await tickets.confirm_order(first.id, paid, db=db_session) == {
        "confirmed": True
    }
    assert (
        await db_session.scalar(
            select(func.count(CommunityExperienceOrder.id)).where(
                CommunityExperienceOrder.offering_id == offering.id
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count(CommunityExperienceParticipant.id)).where(
                CommunityExperienceParticipant.order_id == first.id
            )
        )
        == 1
    )
    person = (
        await db_session.execute(
            select(CommunityExperienceParticipant).where(
                CommunityExperienceParticipant.order_id == first.id
            )
        )
    ).scalar_one()
    assert (
        person.member_id is None
        and person.price_kobo == 6_000_000
        and person.waiver_accepted_at is not None
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_expired_hold_releases_capacity_but_late_payment_cannot_overbook(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    offering = CommunityExperienceOffering(
        name="Expired ticket integration",
        currency="NGN",
        period_start=now.date(),
        period_end=(now + timedelta(days=90)).date(),
        standard_member_fee_kobo=500,
        club_member_fee_kobo=400,
        club_bundle_fee_kobo=300,
        public_guest_fee_kobo=600,
        capacity=1,
    )
    db_session.add(offering)
    await db_session.commit()
    monkeypatch.setattr(
        tickets,
        "live_events",
        AsyncMock(return_value=[{"visibility": "public", "max_capacity": 1}]),
    )
    first = await tickets.create_order(
        offering.id, request_body(), current_user=None, db=db_session
    )
    expired = await db_session.get(CommunityExperienceOrder, first.id)
    expired.expires_at = now - timedelta(minutes=1)
    await db_session.commit()
    second = await tickets.create_order(
        offering.id, request_body(), current_user=None, db=db_session
    )
    await tickets.confirm_order(
        second.id,
        ExperienceOrderConfirm(
            payment_reference=second.payment_reference, amount_kobo=600
        ),
        db=db_session,
    )
    with pytest.raises(HTTPException, match="enough places"):
        await tickets.confirm_order(
            first.id,
            ExperienceOrderConfirm(
                payment_reference=first.payment_reference, amount_kobo=600
            ),
            db=db_session,
        )
