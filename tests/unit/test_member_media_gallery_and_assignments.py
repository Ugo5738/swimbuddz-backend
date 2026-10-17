"""Member gallery, standalone media, and private playback regression checks."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.media_service.routers import vaults


@pytest.mark.asyncio
async def test_approved_private_photo_with_explicit_share_is_viewable():
    member = uuid.uuid4()
    photo = SimpleNamespace(
        id=uuid.uuid4(),
        media_type=vaults.MediaType.IMAGE,
        processing_status="ready",
        soft_deleted_at=None,
        review_status="approved",
        consent_status="cleared",
        session_visibility="selected_members",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid.uuid4()))
    vault = SimpleNamespace(deleted_at=None, status="review")
    assert await vaults._member_can_view_video(
        db, member_id=member, item=photo, vault=vault
    )


@pytest.mark.asyncio
async def test_unreviewed_photo_is_not_viewable_even_with_share():
    photo = SimpleNamespace(
        id=uuid.uuid4(),
        media_type=vaults.MediaType.IMAGE,
        processing_status="ready",
        soft_deleted_at=None,
        review_status="unreviewed",
        consent_status="cleared",
        session_visibility="selected_members",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid.uuid4()))
    vault = SimpleNamespace(deleted_at=None, status="review")
    assert not await vaults._member_can_view_video(
        db, member_id=uuid.uuid4(), item=photo, vault=vault
    )
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_photo_share_is_not_viewable():
    photo = SimpleNamespace(
        id=uuid.uuid4(),
        media_type=vaults.MediaType.IMAGE,
        processing_status="ready",
        soft_deleted_at=None,
        review_status="approved",
        consent_status="cleared",
        session_visibility="selected_members",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    vault = SimpleNamespace(deleted_at=None, status="review")
    assert not await vaults._member_can_view_video(
        db, member_id=uuid.uuid4(), item=photo, vault=vault
    )


@pytest.mark.asyncio
async def test_member_can_only_request_proxy_for_their_own_shared_media(monkeypatch):
    member_id = uuid.uuid4()
    item = SimpleNamespace(
        id=uuid.uuid4(),
        vault_id=uuid.uuid4(),
        media_type=vaults.MediaType.VIDEO,
        proxy_object_key=None,
    )
    actor = SimpleNamespace(member_id=member_id)
    monkeypatch.setattr(vaults, "resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(vaults, "_member_can_view_video", AsyncMock(return_value=False))
    monkeypatch.setattr(vaults, "effective_vault_role", AsyncMock(return_value=None))
    db = SimpleNamespace(
        get=AsyncMock(side_effect=[item, SimpleNamespace(id=item.vault_id)])
    )
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.request_my_shared_preview(
            item.id, current_user=SimpleNamespace(), db=db
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cannot_assign_and_share_unreviewed_video(monkeypatch):
    media_id = uuid.uuid4()
    vault_id = uuid.uuid4()
    video = SimpleNamespace(
        id=media_id,
        review_status="unreviewed",
        processing_status="ready",
        consent_status="cleared",
        media_type=vaults.MediaType.VIDEO,
        session_visibility="selected_members",
    )
    monkeypatch.setattr(
        vaults, "get_vault_or_404", AsyncMock(return_value=SimpleNamespace(id=vault_id))
    )
    query_result = SimpleNamespace(all=lambda: [video])
    db = SimpleNamespace(
        scalars=AsyncMock(side_effect=[query_result, SimpleNamespace(all=lambda: [])]),
        commit=AsyncMock(),
    )
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.assign_media_to_swimmers_or_session(
            vault_id,
            vaults.MediaAssignmentRequest(
                media_item_ids=[media_id],
                member_ids=[uuid.uuid4()],
                grant_access_to_featured=True,
            ),
            current_user=SimpleNamespace(user_id=str(uuid.uuid4())),
            db=db,
        )
    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_approved_image_can_be_shared_in_selected_member_policy(monkeypatch):
    media_id = uuid.uuid4()
    vault_id = uuid.uuid4()
    image = SimpleNamespace(
        id=media_id,
        media_type=vaults.MediaType.IMAGE,
        processing_status="ready",
        review_status="approved",
        consent_status="cleared",
        session_visibility="private",
        allow_member_clips=False,
        allow_member_downloads=False,
        source_session_id=None,
        thumbnail_object_key="private/thumb.jpg",
        proxy_object_key=None,
    )
    monkeypatch.setattr(
        vaults, "get_vault_or_404", AsyncMock(return_value=SimpleNamespace(id=vault_id))
    )
    monkeypatch.setattr(vaults, "write_audit", AsyncMock())
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [image])),
        commit=AsyncMock(),
    )
    result = await vaults.change_session_media_visibility(
        vault_id,
        vaults.SessionMediaPolicyUpdate(
            media_item_ids=[media_id],
            session_visibility="selected_members",
            allow_member_clips=True,
        ),
        current_user=SimpleNamespace(user_id=str(uuid.uuid4())),
        db=db,
    )
    assert result["updated"] == 1
    assert image.session_visibility == "selected_members"
    assert image.allow_member_clips is False
    db.commit.assert_awaited_once()
