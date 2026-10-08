"""Alumni evidence authorization and consent tests."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.academy_service.routers import evidence as module


@pytest.mark.asyncio
async def test_non_owner_cannot_view_another_enrollment():
    enrollment = SimpleNamespace(member_auth_id=str(uuid.uuid4()))
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: enrollment)
        )
    )
    actor = SimpleNamespace(user_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as err:
        await module._own_enrollment(uuid.uuid4(), actor, db)
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_enrollment_returns_not_found():
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    )
    with pytest.raises(HTTPException) as err:
        await module._own_enrollment(
            uuid.uuid4(), SimpleNamespace(user_id=str(uuid.uuid4())), db
        )
    assert err.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code,expected", [(200, None), (404, 400), (500, 503)])
async def test_video_owner_validation_fails_closed(monkeypatch, status_code, expected):
    monkeypatch.setattr(
        module,
        "internal_get",
        AsyncMock(return_value=SimpleNamespace(status_code=status_code)),
    )
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(MEDIA_SERVICE_URL="http://media"),
    )
    if expected is None:
        await module._validate_video_owner(uuid.uuid4(), str(uuid.uuid4()))
    else:
        with pytest.raises(HTTPException) as err:
            await module._validate_video_owner(uuid.uuid4(), str(uuid.uuid4()))
        assert err.value.status_code == expected


def test_recording_date_cannot_be_in_future():
    from datetime import date, timedelta

    with pytest.raises(ValueError):
        module.MilestoneEvidenceCreate(
            milestone_id=uuid.uuid4(),
            video_media_id=uuid.uuid4(),
            kind="continued_progress",
            recorded_on=date.today() + timedelta(days=1),
        )
