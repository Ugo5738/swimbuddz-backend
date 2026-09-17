from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from services.members_service.routers import club_plan_admin as admin
from services.members_service.schemas.club_merchandising import (
    AttachClubExperienceRequest,
)
from services.members_service.services import experience_events


@pytest.fixture
def setup(monkeypatch):
    plan = SimpleNamespace(
        id=uuid4(),
        club_id=uuid4(),
        published_at=datetime.now(timezone.utc),
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        currency="NGN",
        community_experience_offering_id=None,
        community_experience_fee_kobo=0,
        community_experience_default_selected=False,
        club_fee_kobo=6_760_000,
        session_links=[object()],
    )
    offering = SimpleNamespace(
        id=uuid4(),
        period_start=plan.period_start,
        period_end=plan.period_end,
        currency="NGN",
        is_active=True,
        club_bundle_fee_kobo=3_000_000,
    )
    monkeypatch.setattr(admin, "_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(admin, "hydrate_schedules", AsyncMock())
    monkeypatch.setattr(admin, "plan_response", Mock(side_effect=lambda p, _: p))
    live = AsyncMock()
    monkeypatch.setattr(experience_events, "live_events", live)
    db = SimpleNamespace(
        get=AsyncMock(side_effect=[object(), offering]), commit=AsyncMock()
    )
    return plan, offering, db, live


@pytest.mark.asyncio
async def test_attach_published_plan_is_optional_and_preserves_club_terms(setup):
    plan, offering, db, live = setup
    old_links, published = plan.session_links, plan.published_at
    await admin.attach_plan_experience(
        plan.id, AttachClubExperienceRequest(offering_id=offering.id), db
    )
    assert plan.community_experience_offering_id == offering.id
    assert plan.community_experience_fee_kobo == 3_000_000
    assert not plan.community_experience_default_selected
    assert plan.club_fee_kobo == 6_760_000
    assert plan.session_links is old_links
    assert plan.published_at == published
    live.assert_awaited_once_with(offering, for_sale=True)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["quarter", "currency", "inactive", "missing"])
async def test_reject_invalid_offering(setup, change):
    plan, offering, db, _ = setup
    if change == "quarter":
        offering.period_start = date(2027, 1, 1)
    elif change == "currency":
        offering.currency = "USD"
    elif change == "inactive":
        offering.is_active = False
    else:
        db.get.side_effect = [object(), None]
    with pytest.raises(HTTPException) as error:
        await admin.attach_plan_experience(
            plan.id, AttachClubExperienceRequest(offering_id=offering.id), db
        )
    assert error.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_link_cannot_be_replaced_but_same_link_retry_is_safe(setup):
    plan, offering, db, live = setup
    plan.community_experience_offering_id = offering.id
    await admin.attach_plan_experience(
        plan.id, AttachClubExperienceRequest(offering_id=offering.id), db
    )
    live.assert_not_awaited()
    db.get.side_effect = [object()]
    with pytest.raises(HTTPException) as error:
        await admin.attach_plan_experience(
            plan.id, AttachClubExperienceRequest(offering_id=uuid4()), db
        )
    assert error.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_event_does_not_leave_a_partial_link(setup):
    plan, offering, db, live = setup
    live.side_effect = HTTPException(409, "Event unavailable")
    with pytest.raises(HTTPException):
        await admin.attach_plan_experience(
            plan.id, AttachClubExperienceRequest(offering_id=offering.id), db
        )
    assert plan.community_experience_offering_id is None
    db.commit.assert_not_awaited()
