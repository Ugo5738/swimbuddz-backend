"""One publication path for included swims; no implicit draft regeneration."""

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.members_service.routers import club_plan_admin as plans, clubs
from services.sessions_service.models import SessionStatus
from services.sessions_service.routers import templates
from services.sessions_service.schemas.templates import GenerateSessionsRequest
from services.sessions_service.services import club_generation
from tests.unit.test_club_recommendations_and_operations import result, template, quote


@pytest.mark.asyncio
async def test_generic_generate_rejects_plan_inclusions_before_side_effects(
    monkeypatch,
):
    saved = template()
    db = NS(
        execute=AsyncMock(return_value=result([saved])), add=Mock(), commit=AsyncMock()
    )
    scope = AsyncMock()
    generate = AsyncMock()
    monkeypatch.setattr(templates, "require_valid_club_scope", scope)
    monkeypatch.setattr(club_generation, "club_session_from_template", generate)
    with pytest.raises(HTTPException) as error:
        await templates.generate_sessions(
            saved.id, GenerateSessionsRequest(weeks=2), db, None
        )
    assert error.value.status_code == 409
    assert "Club quarter recommendations" in error.value.detail
    scope.assert_not_awaited()
    generate.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("mode", ["active_club", "paid_addon"])
@pytest.mark.asyncio
async def test_generic_generate_retains_operational_club_templates(mode, monkeypatch):
    saved = template()
    saved.club_access_mode = mode
    saved.ride_share_fee = 100000
    saved.ride_share_config = []
    db = NS(
        execute=AsyncMock(return_value=result([saved])),
        get=AsyncMock(return_value=None),
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(templates, "require_valid_club_scope", AsyncMock())
    monkeypatch.setattr(
        club_generation, "internal_post", AsyncMock(return_value=quote(3000))
    )
    notify = AsyncMock()
    monkeypatch.setattr(templates, "trigger_session_published_notifications", notify)
    monkeypatch.setattr(
        templates,
        "materialise_opportunities_from_session_template",
        AsyncMock(return_value={"created_count": 0}),
    )
    response = await templates.generate_sessions(
        saved.id, GenerateSessionsRequest(weeks=1, skip_conflicts=False), db, None
    )
    assert response["created"] == 1
    swim = db.add.call_args.args[0]
    assert swim.club_access_mode == mode
    assert swim.status == SessionStatus.SCHEDULED and swim.published_at is not None
    assert swim.pool_fee == 450000 and swim.ride_share_fee == saved.ride_share_fee
    assert swim.club_id == saved.club_id
    notify.assert_awaited_once()


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.asyncio
async def test_existing_draft_rejects_new_recommendation_inputs_without_mutation(
    linked, monkeypatch
):
    club = NS(
        id=uuid4(), is_active=True, default_pool_id=uuid4(), operating_area_id=uuid4()
    )
    draft = NS(
        id=uuid4(),
        published_at=None,
        session_links=[NS(session_id=uuid4())] if linked else [],
        club_fee_kobo=6500000,
    )
    original = dict(vars(draft))
    db = NS(
        execute=AsyncMock(side_effect=[result([club]), result([draft])]),
        commit=AsyncMock(),
        add=Mock(),
    )
    monkeypatch.setattr(clubs, "_validate_club_pool_area", AsyncMock())
    generate = AsyncMock()
    monkeypatch.setattr(plans, "internal_post", generate)
    body = plans.QuarterRecommendationRequest(
        club_id=club.id,
        year=2026,
        quarter=4,
        excluded_dates=[date(2026, 12, 5)],
        pricing_settings={"pricing_expected_attendees": 15, "margin_value": 1500},
    )
    with pytest.raises(HTTPException) as error:
        await plans.recommend_quarter(body, db)
    assert error.value.status_code == 409
    assert "Open the existing draft to edit it" in error.value.detail
    assert vars(draft) == original
    generate.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_recommendation_returns_immutable_existing_plan(monkeypatch):
    club = NS(
        id=uuid4(), is_active=True, default_pool_id=uuid4(), operating_area_id=uuid4()
    )
    published = NS(id=uuid4(), published_at=datetime.now(timezone.utc))
    db = NS(
        execute=AsyncMock(side_effect=[result([club]), result([published])]),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(clubs, "_validate_club_pool_area", AsyncMock())
    monkeypatch.setattr(plans, "hydrate_schedules", AsyncMock())
    expected = {"id": str(published.id), "club_fee_kobo": 6500000}
    monkeypatch.setattr(plans, "plan_response", Mock(return_value=expected))
    generate = AsyncMock()
    monkeypatch.setattr(plans, "internal_post", generate)
    body = plans.QuarterRecommendationRequest(
        club_id=club.id, year=2026, quarter=4, pricing_settings={"margin_value": 999}
    )
    assert await plans.recommend_quarter(body, db) == expected
    generate.assert_not_awaited()
    db.commit.assert_not_awaited()
