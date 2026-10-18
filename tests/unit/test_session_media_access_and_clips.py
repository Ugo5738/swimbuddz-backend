"""Privacy and clipping contracts for session media."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from services.media_service.schemas.vault import ClipCreate, SessionMediaPolicyUpdate
from services.media_service.routers import vaults
from services.media_service.tasks.worker import (
    WorkerSettings,
    task_build_member_vault_clip,
    task_cleanup_member_vault_clips,
)


def test_member_clip_duration_is_bounded():
    assert ClipCreate(start_ms=3000, end_ms=93000).end_ms == 93000
    assert ClipCreate(start_ms=0, end_ms=3_600_000).end_ms == 3_600_000
    with pytest.raises(ValidationError):
        ClipCreate(start_ms=5000, end_ms=5000)
    assert ClipCreate(start_ms=1000, end_ms=92000).end_ms == 92000
    with pytest.raises(ValidationError):
        ClipCreate(start_ms=5000, end_ms=5200)


def test_media_audience_values_are_restricted():
    video_id = uuid.uuid4()
    policy = SessionMediaPolicyUpdate(
        media_item_ids=[video_id],
        session_visibility="session_participants",
        allow_member_clips=True,
    )
    assert policy.media_item_ids == [video_id]
    with pytest.raises(ValidationError):
        SessionMediaPolicyUpdate(media_item_ids=[video_id], session_visibility="public")


@pytest.mark.asyncio
async def test_unapproved_video_cannot_be_seen_even_by_attendees(monkeypatch):
    member_id = uuid.uuid4()
    item = SimpleNamespace(
        id=uuid.uuid4(),
        media_type=vaults.MediaType.VIDEO,
        processing_status="ready",
        soft_deleted_at=None,
        review_status="unreviewed",
        consent_status="cleared",
        session_visibility="session_participants",
        source_session_id=uuid.uuid4(),
    )
    vault = SimpleNamespace(session_id=None, deleted_at=None, status="open")
    attendance = AsyncMock(return_value=[{"status": "present", "role": "swimmer"}])
    monkeypatch.setattr(vaults, "get_member_attendance", attendance)
    assert not await vaults._member_can_view_video(
        AsyncMock(), member_id=member_id, item=item, vault=vault
    )
    attendance.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_actual_swimmers_can_see_participant_video(monkeypatch):
    member_id = uuid.uuid4()
    session_id = uuid.uuid4()
    item = SimpleNamespace(
        id=uuid.uuid4(),
        media_type=vaults.MediaType.VIDEO,
        processing_status="ready",
        soft_deleted_at=None,
        review_status="approved",
        consent_status="cleared",
        session_visibility="session_participants",
        source_session_id=session_id,
    )
    vault = SimpleNamespace(id=uuid.uuid4(), session_id=session_id, deleted_at=None, status="open", attendee_can_view=True)
    attendance = AsyncMock(return_value=[{"status": "absent", "role": "swimmer"}])
    monkeypatch.setattr(vaults, "get_member_attendance", attendance)
    db = AsyncMock()
    db.scalar.return_value = None
    assert not await vaults._member_can_view_video(
        db, member_id=member_id, item=item, vault=vault
    )
    attendance.return_value = [{"status": "present", "role": "coach"}]
    assert not await vaults._member_can_view_video(
        db, member_id=member_id, item=item, vault=vault
    )
    attendance.return_value = [{"status": "present", "role": "swimmer"}]
    assert await vaults._member_can_view_video(
        db, member_id=member_id, item=item, vault=vault
    )


def test_clip_worker_and_cleanup_are_registered():
    assert task_build_member_vault_clip in WorkerSettings.functions
    assert task_cleanup_member_vault_clips in WorkerSettings.functions


@pytest.mark.asyncio
async def test_admin_cannot_share_unapproved_session_video(monkeypatch):
    item_id = uuid.uuid4()
    vault_id = uuid.uuid4()
    item = SimpleNamespace(
        id=item_id,
        source_session_id=uuid.uuid4(),
        media_type=vaults.MediaType.VIDEO,
        review_status="unreviewed",
        consent_status="unreviewed",
        processing_status="ready",
        soft_deleted_at=None,
    )
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [item]))
    )
    monkeypatch.setattr(
        vaults,
        "get_vault_or_404",
        AsyncMock(return_value=SimpleNamespace(id=vault_id, session_id=None)),
    )
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.change_session_media_visibility(
            vault_id,
            SessionMediaPolicyUpdate(
                media_item_ids=[item_id],
                session_visibility="session_participants",
                allow_member_clips=True,
            ),
            current_user=SimpleNamespace(user_id=str(uuid.uuid4())),
            db=db,
        )
    assert exc.value.status_code == 422
    assert "consent" in exc.value.detail


@pytest.mark.asyncio
async def test_member_cannot_clip_unshared_recording(monkeypatch):
    member_id = uuid.uuid4()
    video = SimpleNamespace(
        id=uuid.uuid4(), vault_id=uuid.uuid4(), allow_member_clips=False
    )
    db = SimpleNamespace(get=AsyncMock(return_value=video))
    monkeypatch.setattr(
        vaults,
        "resolve_actor",
        AsyncMock(return_value=SimpleNamespace(member_id=member_id)),
    )
    monkeypatch.setattr(
        vaults,
        "get_vault_or_404",
        AsyncMock(return_value=SimpleNamespace(id=video.vault_id)),
    )
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.request_member_clip(
            video.id,
            ClipCreate(start_ms=0, end_ms=10000),
            current_user=SimpleNamespace(),
            db=db,
        )
    assert exc.value.status_code == 403
