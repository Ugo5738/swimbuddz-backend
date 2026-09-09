"""One publication path for included swims; no implicit draft regeneration."""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.members_service.routers import club_plan_admin as plans, clubs
from services.members_service.models import ClubPlanVersion
from services.members_service.services import club_plan_schedule
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


@pytest.mark.asyncio
async def test_configured_draft_rejects_new_recommendation_inputs_without_mutation(
    monkeypatch,
):
    club = NS(
        id=uuid4(), is_active=True, default_pool_id=uuid4(), operating_area_id=uuid4()
    )
    draft = NS(
        id=uuid4(),
        published_at=None,
        session_links=[NS(session_id=uuid4())],
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


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.asyncio
async def test_published_recommendation_returns_immutable_existing_plan(
    monkeypatch, linked
):
    club = NS(
        id=uuid4(), is_active=True, default_pool_id=uuid4(), operating_area_id=uuid4()
    )
    published = NS(
        id=uuid4(),
        published_at=datetime.now(timezone.utc),
        session_links=[NS(session_id=uuid4())] if linked else [],
    )
    original = dict(vars(published))
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
    assert vars(published) == original
    generate.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.fixture
def recommendation_setup(monkeypatch):
    club = NS(
        id=uuid4(),
        name="Yaba",
        is_active=True,
        default_pool_id=uuid4(),
        operating_area_id=uuid4(),
        default_session_day="sat",
        default_session_time=time(9),
        default_session_duration_minutes=90,
    )
    rows = [
        {
            "id": str(uuid4()),
            "title": "Club practice",
            "club_id": str(club.id),
            "pool_id": str(club.default_pool_id),
            "pod_id": None,
            "session_type": "club",
            "club_access_mode": "plan_included",
            "status": "draft",
            "starts_at": f"2026-10-{day:02d}T09:00:00+01:00",
            "ends_at": f"2026-10-{day:02d}T10:30:00+01:00",
            "fee_kobo": fee,
        }
        for day, fee in [(3, 450000), (10, 550000)]
    ]
    generate = AsyncMock(return_value=NS(status_code=200, json=lambda: rows))
    monkeypatch.setattr(plans, "internal_post", generate)
    monkeypatch.setattr(clubs, "_validate_club_pool_area", AsyncMock())
    monkeypatch.setattr(
        club_plan_schedule, "fetch_schedule", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(plans, "hydrate_schedules", AsyncMock())
    monkeypatch.setattr(
        plans, "plan_response", Mock(side_effect=lambda plan, club: plan)
    )
    body = plans.QuarterRecommendationRequest(
        club_id=club.id,
        year=2026,
        quarter=4,
        template_id=uuid4(),
        capacity=15,
        excluded_dates=[date(2026, 12, 5)],
        pricing_settings={"pricing_expected_attendees": 15, "margin_value": 1500},
    )
    return club, rows, generate, body


def recommendation_db(club, existing):
    return NS(
        execute=AsyncMock(
            side_effect=[result([club]), result([existing] if existing else [])]
        ),
        commit=AsyncMock(),
        add=Mock(),
    )


def empty_draft(club, *, override=False):
    return ClubPlanVersion(
        id=uuid4(),
        club_id=club.id,
        name="Admin's saved Q4 draft",
        currency="NGN",
        pool_id=club.default_pool_id,
        operating_area_id=club.operating_area_id,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        effective_from=date(2026, 9, 1),
        effective_to=date(2026, 12, 1),
        premium_venue_note="Keep this note",
        published_at=None,
        is_active=False,
        session_links=[],
        sessions_included=0,
        recommended_fee_kobo=0,
        club_fee_kobo=600000 if override else 0,
        capacity=8,
        minimum_entry_sessions=3,
        refreshments_included=False,
        community_experience_offering_id=uuid4(),
        community_experience_fee_kobo=3000000,
        community_experience_default_selected=True,
        source_plan_id=uuid4(),
    )


def assert_generated_schedule(draft, rows):
    assert [str(link.session_id) for link in draft.session_links] == [
        row["id"] for row in rows
    ]
    assert draft.sessions_included == 2 and draft.recommended_fee_kobo == 1000000
    assert draft.published_at is None and draft.is_active is False


@pytest.mark.asyncio
async def test_recommendation_with_no_draft_creates_one_unpublished_plan(
    recommendation_setup,
):
    club, rows, generate, body = recommendation_setup
    db = recommendation_db(club, None)
    created = await plans.recommend_quarter(body, db)
    assert_generated_schedule(created, rows)
    assert created.club_fee_kobo == 1000000 and created.club_id == club.id
    assert created.source_template_id == body.template_id and created.capacity == 15
    db.add.assert_called_once_with(created)
    db.commit.assert_awaited_once()
    generate.assert_awaited_once()


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.asyncio
async def test_recommendation_populates_empty_draft_in_place(
    recommendation_setup, override
):
    club, rows, generate, body = recommendation_setup
    existing = empty_draft(club, override=override)
    preserved = {
        key: getattr(existing, key)
        for key in (
            "id",
            "name",
            "period_start",
            "period_end",
            "effective_from",
            "effective_to",
            "premium_venue_note",
            "community_experience_offering_id",
            "community_experience_fee_kobo",
            "community_experience_default_selected",
            "source_plan_id",
        )
    }
    db = recommendation_db(club, existing)
    populated = await plans.recommend_quarter(body, db)
    assert populated is existing
    assert_generated_schedule(populated, rows)
    assert populated.club_fee_kobo == (600000 if override else 1000000)
    assert all(getattr(populated, key) == value for key, value in preserved.items())
    assert populated.source_template_id == body.template_id and populated.capacity == 15
    assert (
        populated.minimum_entry_sessions == 3
        and populated.refreshments_included is False
    )
    payload = generate.call_args.kwargs["json"]
    assert payload["pricing_settings"] == body.pricing_settings
    assert payload["excluded_dates"] == ["2026-12-05"] and payload["capacity"] == 15
    assert "FOR UPDATE" in str(db.execute.call_args_list[1].args[0])
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_draft_uses_saved_settings_when_request_omits_them(
    recommendation_setup,
):
    club, rows, generate, _ = recommendation_setup
    existing = empty_draft(club)
    body = plans.QuarterRecommendationRequest(club_id=club.id, year=2026, quarter=4)
    populated = await plans.recommend_quarter(body, recommendation_db(club, existing))
    assert (
        populated.capacity,
        populated.minimum_entry_sessions,
        populated.refreshments_included,
    ) == (8, 3, False)
    assert populated.source_template_id is not None
    assert generate.call_args.kwargs["json"]["capacity"] == 8


@pytest.mark.asyncio
async def test_failed_generation_does_not_partially_populate_empty_draft(
    recommendation_setup,
):
    club, _, generate, body = recommendation_setup
    existing = empty_draft(club)
    generate.return_value = NS(
        status_code=503, json=lambda: {"detail": "Rates unavailable"}
    )
    db = recommendation_db(club, existing)
    with pytest.raises(HTTPException) as error:
        await plans.recommend_quarter(body, db)
    assert error.value.status_code == 503
    assert existing.session_links == [] and existing.club_fee_kobo == 0
    assert existing.capacity == 8 and existing.published_at is None
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
