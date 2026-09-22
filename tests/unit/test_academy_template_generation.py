"""Academy templates must retain both enrollment scope and explicit billing."""

from datetime import date, time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.sessions_service.models import SessionTemplate, SessionType
from services.sessions_service.models._validators import validate_session_discriminator
from services.sessions_service.routers import templates
from services.sessions_service.schemas.templates import (
    GenerateSessionsRequest,
    SessionTemplateCreate,
    SessionTemplateUpdate,
)
from services.sessions_service.services import template_context


def saved_template(**overrides):
    return SessionTemplate(
        **{
            "id": uuid4(),
            "title": "Practice",
            "session_type": SessionType.COHORT_CLASS,
            "cohort_id": uuid4(),
            "cohort_fee_mode": "paid_extra",
            "is_active": True,
            "pool_fee": 1500000,
            "ride_share_fee": 0,
            "capacity": 2,
            "pool_id": uuid4(),
            "day_of_week": 6,
            "start_time": time(16),
            "duration_minutes": 60,
            "club_access_mode": "plan_included",
            **overrides,
        }
    )


def database(saved):
    return NS(
        execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: saved)),
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


def academy_response(status=200):
    return httpx.Response(
        status, request=httpx.Request("GET", "http://academy/cohorts/test")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["included", "paid_extra"])
@pytest.mark.parametrize(
    "window",
    [
        {"weeks": 2},
        {"from_date": "2026-10-01", "to_date": "2026-10-14"},
        {"dates": ["2026-10-04", "2026-10-11"]},
    ],
)
async def test_generated_academy_class_preserves_cohort_price_and_billing(
    mode, window, monkeypatch
):
    saved = saved_template(cohort_fee_mode=mode)
    db = database(saved)
    get = AsyncMock(return_value=academy_response())
    monkeypatch.setattr(template_context, "internal_get", get)
    notify = AsyncMock()
    monkeypatch.setattr(templates, "trigger_session_published_notifications", notify)
    monkeypatch.setattr(
        templates,
        "materialise_opportunities_from_session_template",
        AsyncMock(return_value={"created_count": 0}),
    )
    response = await templates.generate_sessions(
        saved.id, GenerateSessionsRequest(**window, skip_conflicts=False), db, None
    )
    assert response["created"] == 2
    for call in db.add.call_args_list:
        session = call.args[0]
        assert session.cohort_id == saved.cohort_id
        assert session.cohort_fee_mode == mode
        assert session.pool_fee == 1500000  # Already kobo; never converted twice.
        assert session.capacity == 2 and session.template_id == saved.id
        validate_session_discriminator(
            session_type=session.session_type,
            cohort_id=session.cohort_id,
            event_id=session.event_id,
            club_id=session.club_id,
            pod_id=session.pod_id,
        )
    db.commit.assert_awaited_once()
    assert notify.await_count == 2
    assert get.call_args.kwargs["path"] == f"/academy/cohorts/{saved.cohort_id}"


@pytest.mark.asyncio
async def test_monthly_paid_extra_classes_keep_cohort_and_price(monkeypatch):
    saved = saved_template(
        frequency="monthly",
        interval=1,
        week_of_month=1,
        starts_on=date(2026, 10, 1),
    )
    db = database(saved)
    monkeypatch.setattr(
        template_context, "internal_get", AsyncMock(return_value=academy_response())
    )
    monkeypatch.setattr(
        templates, "trigger_session_published_notifications", AsyncMock()
    )
    monkeypatch.setattr(
        templates,
        "materialise_opportunities_from_session_template",
        AsyncMock(return_value={}),
    )
    response = await templates.generate_sessions(
        saved.id,
        GenerateSessionsRequest(
            from_date="2026-10-01", to_date="2026-11-30", skip_conflicts=False
        ),
        db,
        None,
    )
    assert [item["date"] for item in response["sessions"]] == [
        "2026-10-04",
        "2026-11-01",
    ]
    for call in db.add.call_args_list:
        assert call.args[0].cohort_id == saved.cohort_id
        assert call.args[0].cohort_fee_mode == "paid_extra"
        assert call.args[0].pool_fee == 1500000


@pytest.mark.asyncio
async def test_legacy_cohort_template_fails_clearly_before_flush(monkeypatch):
    saved = saved_template(cohort_id=None)
    db = database(saved)
    lookup = AsyncMock()
    monkeypatch.setattr(template_context, "internal_get", lookup)
    with pytest.raises(HTTPException) as caught:
        await templates.generate_sessions(
            saved.id, GenerateSessionsRequest(weeks=1), db, None
        )
    assert caught.value.status_code == 422
    assert "choose its cohort" in caught.value.detail
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(404, 422), (500, 503)])
async def test_missing_or_unavailable_cohort_cannot_generate(
    status, expected, monkeypatch
):
    saved = saved_template()
    db = database(saved)
    monkeypatch.setattr(
        template_context,
        "internal_get",
        AsyncMock(return_value=academy_response(status)),
    )
    with pytest.raises(HTTPException) as caught:
        await templates.generate_sessions(
            saved.id, GenerateSessionsRequest(weeks=1), db, None
        )
    assert caught.value.status_code == expected
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cohort_timeout_is_retryable_not_internal_error(monkeypatch):
    monkeypatch.setattr(
        template_context,
        "internal_get",
        AsyncMock(side_effect=httpx.ReadTimeout("timeout")),
    )
    with pytest.raises(HTTPException) as caught:
        await template_context.require_template_cohort(uuid4())
    assert caught.value.status_code == 503


def test_new_academy_template_requires_cohort_and_explicit_extra_mode():
    body = dict(
        title="Practice",
        session_type="cohort_class",
        pool_id=uuid4(),
        day_of_week=6,
        start_time="16:00",
        duration_minutes=60,
        pool_fee=15000,
    )
    with pytest.raises(ValidationError, match="choose its cohort"):
        SessionTemplateCreate(**body)
    assert (
        SessionTemplateCreate(**body, cohort_id=uuid4()).cohort_fee_mode == "included"
    )
    assert (
        SessionTemplateCreate(
            **body, cohort_id=uuid4(), cohort_fee_mode="paid_extra"
        ).cohort_fee_mode
        == "paid_extra"
    )


@pytest.mark.parametrize(
    "context", [{"cohort_id": uuid4()}, {"cohort_fee_mode": "paid_extra"}]
)
def test_non_academy_template_cannot_carry_academy_context(context):
    with pytest.raises(ValidationError, match="Only Academy"):
        SessionTemplateCreate(
            title="Swim",
            session_type="community",
            location="rowe_park_pool",
            day_of_week=6,
            start_time="16:00",
            duration_minutes=60,
            **context,
        )


@pytest.mark.asyncio
async def test_update_preserves_extra_mode_and_validates_before_writing(monkeypatch):
    saved = saved_template()
    db = database(saved)
    monkeypatch.setattr(
        template_context, "internal_get", AsyncMock(return_value=academy_response())
    )
    monkeypatch.setattr(
        templates.SessionTemplateResponse,
        "model_validate",
        Mock(side_effect=lambda value: value),
    )
    response = await templates.update_template(
        saved.id, SessionTemplateUpdate(pool_fee=16000), db, None
    )
    assert response.cohort_fee_mode == "paid_extra" and response.pool_fee == 1600000
    db.commit.reset_mock()
    with pytest.raises(HTTPException) as caught:
        await templates.update_template(
            saved.id, SessionTemplateUpdate(cohort_id=None), db, None
        )
    assert caught.value.status_code == 422
    db.commit.assert_not_awaited()
    assert saved.cohort_id is not None


@pytest.mark.asyncio
async def test_switching_to_community_clears_cohort_and_extra_mode(monkeypatch):
    saved = saved_template()
    monkeypatch.setattr(
        templates.SessionTemplateResponse,
        "model_validate",
        Mock(side_effect=lambda value: value),
    )
    response = await templates.update_template(
        saved.id, SessionTemplateUpdate(session_type="community"), database(saved), None
    )
    assert response.cohort_id is None and response.cohort_fee_mode == "included"


@pytest.mark.asyncio
async def test_unconfigured_legacy_template_can_still_be_archived(monkeypatch):
    saved = saved_template(cohort_id=None)
    monkeypatch.setattr(
        templates.SessionTemplateResponse,
        "model_validate",
        Mock(side_effect=lambda value: value),
    )
    response = await templates.update_template(
        saved.id, SessionTemplateUpdate(is_active=False), database(saved), None
    )
    assert response.is_active is False and response.auto_generate is False


def test_template_context_migration_preserves_existing_sessions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_mock_engine
    from unittest.mock import patch

    scripts = ScriptDirectory.from_config(
        Config("services/sessions_service/alembic.ini")
    )
    assert len(scripts.get_heads()) == 1
    migration = scripts.get_revision("f8a0b2c4d637")
    assert migration.down_revision == "c6e8a0b2d914"
    statements = []
    engine = create_mock_engine(
        "postgresql://",
        lambda sql, *args, **kwargs: statements.append(
            str(sql.compile(dialect=engine.dialect))
        ),
    )
    with patch.object(
        migration.module, "op", Operations(MigrationContext.configure(engine.connect()))
    ):
        migration.module.upgrade()
        migration.module.downgrade()
    ddl = "\n".join(statements)
    assert "ADD COLUMN cohort_id UUID" in ddl
    assert "DEFAULT 'included' NOT NULL" in ddl
    assert "ck_session_templates_cohort_context" in ddl
    assert "DROP COLUMN cohort_id" in ddl
    assert "UPDATE " not in ddl and "DELETE " not in ddl
    assert "ALTER TABLE sessions " not in ddl
