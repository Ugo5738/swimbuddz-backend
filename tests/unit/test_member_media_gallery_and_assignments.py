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
        id=uuid.uuid4(), media_type=vaults.MediaType.IMAGE,
        processing_status="ready", soft_deleted_at=None, review_status="approved",
        consent_status="cleared", session_visibility="selected_members",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid.uuid4()))
    vault = SimpleNamespace(deleted_at=None, status="review")
    assert await vaults._member_can_view_video(db, member_id=member, item=photo, vault=vault)


@pytest.mark.asyncio
async def test_unreviewed_photo_is_not_viewable_even_with_share():
    photo = SimpleNamespace(
        id=uuid.uuid4(), media_type=vaults.MediaType.IMAGE,
        processing_status="ready", soft_deleted_at=None, review_status="unreviewed",
        consent_status="cleared", session_visibility="selected_members",
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
        id=uuid.uuid4(), media_type=vaults.MediaType.IMAGE,
        processing_status="ready", soft_deleted_at=None, review_status="approved",
        consent_status="cleared", session_visibility="selected_members",
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
        id=uuid.uuid4(), vault_id=uuid.uuid4(), media_type=vaults.MediaType.VIDEO,
        proxy_object_key=None,
    )
    actor = SimpleNamespace(member_id=member_id)
    monkeypatch.setattr(vaults, "resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(vaults, "_member_can_view_video", AsyncMock(return_value=False))
    db = SimpleNamespace(get=AsyncMock(side_effect=[item, SimpleNamespace(id=item.vault_id)]))
    with pytest.raises(vaults.HTTPException) as exc:
        await vaults.request_my_shared_preview(
            item.id, current_user=SimpleNamespace(), db=db
        )
    assert exc.value.status_code == 403
