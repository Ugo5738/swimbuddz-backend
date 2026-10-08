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


@pytest.mark.asyncio
async def test_withdrawing_publication_permission_revokes_showcase(monkeypatch):
    user_id = uuid.uuid4()
    enrollment_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    evidence = SimpleNamespace(
        id=evidence_id,
        enrollment_id=enrollment_id,
        approved_for_public=True,
        publication_consent_at=object(),
        public_display_name="Alex",
        showcase_approved_at=object(),
    )
    monkeypatch.setattr(module, "_own_enrollment", AsyncMock(return_value=object()))
    db = SimpleNamespace(
        get=AsyncMock(return_value=evidence),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    payload = module.PublicationConsentRequest(consent=False)
    await module.update_publication_consent(
        enrollment_id, evidence_id, payload,
        SimpleNamespace(user_id=user_id), db,
    )
    assert evidence.publication_consent_at is None
    assert evidence.approved_for_public is False
    assert evidence.public_display_name is None
    assert evidence.showcase_approved_at is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_showcase_cannot_issue_video_url():
    evidence = SimpleNamespace(
        approved_for_public=False,
        publication_consent_at=None,
        showcase_approved_at=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=evidence))
    with pytest.raises(HTTPException) as exc:
        await module.play_showcase_video(uuid.uuid4(), db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_approve_contact_consent_as_publication_consent():
    evidence = SimpleNamespace(
        consent_to_share=True,
        publication_consent_at=None,
        approved_for_public=False,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=evidence))
    payload = module.EvidenceShowcaseRequest(
        approve=True,
        review_notes="Only contact consent is recorded.",
        publication_consent_confirmed=True,
    )
    with pytest.raises(HTTPException) as exc:
        await module.review_showcase(
            uuid.uuid4(), payload, SimpleNamespace(), db,
        )
    assert exc.value.status_code == 409
