"""Vault audience defaults and member full-download authorization contracts."""

import uuid
from datetime import date, datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from services.media_service.schemas.vault import (
    VaultCreate,
    VaultUpdate,
    SessionMediaPolicyUpdate,
)


def create(**kw):
    now = datetime.now(timezone.utc)
    return VaultCreate(
        title="Week 4 – freestyle",
        capture_date=date.today(),
        session_id=kw.pop("session_id", uuid.uuid4()),
        upload_opens_at=now,
        upload_closes_at=now + timedelta(days=1),
        **kw,
    )


def test_session_defaults_remain_private_until_media_is_approved():
    data = create(
        default_session_visibility="session_participants",
        default_allow_member_clips=True,
    )
    assert data.default_session_visibility == "session_participants"
    assert data.default_allow_member_clips is True


def test_standalone_cannot_expose_attendee_audience():
    with pytest.raises(ValidationError, match="linked scheduled session"):
        create(session_id=None, default_session_visibility="session_participants")


def test_private_vault_never_inherits_download_permissions():
    data = create(
        default_session_visibility="private",
        default_allow_member_clips=True,
        default_allow_member_downloads=True,
    )
    assert data.default_allow_member_clips is False
    assert data.default_allow_member_downloads is False


def test_existing_media_change_must_be_explicit():
    payload = VaultUpdate(default_session_visibility="session_participants")
    assert payload.apply_defaults_to_existing is False


def test_media_policy_exposes_original_download_setting():
    payload = SessionMediaPolicyUpdate(
        media_item_ids=[uuid.uuid4()],
        session_visibility="selected_members",
        allow_member_clips=True,
        allow_member_downloads=True,
    )
    assert payload.allow_member_downloads is True


@pytest.mark.asyncio
async def test_member_full_download_denied_without_recording_permission(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.media_service.routers import vaults

    member_id = uuid.uuid4()
    item = SimpleNamespace(
        id=uuid.uuid4(), vault_id=uuid.uuid4(),
        object_key="private/test.mp4", allow_member_downloads=False,
    )
    actor = SimpleNamespace(member_id=member_id, auth_id=uuid.uuid4())
    monkeypatch.setattr(vaults, "resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(vaults, "get_vault_or_404", AsyncMock(return_value=SimpleNamespace(id=item.vault_id)))
    db = SimpleNamespace(get=AsyncMock(return_value=item))
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.authorize_member_video_download(
            item.id, request=SimpleNamespace(), current_user=SimpleNamespace(), db=db,
        )
    assert exc.value.status_code == 403
