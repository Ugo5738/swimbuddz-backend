import uuid
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from libs.auth.models import AuthUser
from services.media_service.models import MediaItem, MediaType
from services.media_service.routers import media as media_router
from services.media_service.services.image_variants import generate_image_variant
from services.media_service.services.storage import BucketType


def _split_image() -> bytes:
    image = Image.new("RGB", (200, 100), "blue")
    for x in range(100):
        for y in range(100):
            image.putpixel((x, y), (255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_profile_variant_uses_selected_focus_area_and_exact_preset_size():
    generated = generate_image_variant(
        _split_image(),
        purpose="profile_photo",
        crop_x=0,
        crop_y=0,
        crop_width=0.5,
        crop_height=1,
    )

    with Image.open(BytesIO(generated.data)) as output:
        assert output.size == (800, 800)
        red, green, blue = output.getpixel((400, 400))
        assert red > 240
        assert green < 20
        assert blue < 20


def test_content_variant_rejects_crop_with_wrong_aspect_ratio():
    with pytest.raises(HTTPException, match="aspect ratio"):
        generate_image_variant(
            _split_image(),
            purpose="content_image",
            crop_x=0,
            crop_y=0,
            crop_width=0.5,
            crop_height=1,
        )


def test_variant_rejects_crop_outside_source_bounds():
    with pytest.raises(HTTPException, match="within the source image"):
        generate_image_variant(
            _split_image(),
            purpose="profile_photo",
            crop_x=0.6,
            crop_y=0,
            crop_width=0.5,
            crop_height=1,
        )


class _FakeDb:
    def __init__(self):
        self.items = []
        self.flushed = False
        self.committed = False

    def add_all(self, items):
        self.items.extend(items)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        raise AssertionError("rollback was not expected")


@pytest.mark.asyncio
async def test_adjusted_upload_preserves_original_and_returns_linked_variant(
    monkeypatch,
):
    upload = AsyncMock(
        side_effect=[
            ("https://cdn.example.com/original.png", "original-thumb.png"),
            ("https://cdn.example.com/variant.jpg", "variant-thumb.jpg"),
        ]
    )
    monkeypatch.setattr(media_router.storage_service, "upload_media", upload)
    response_builder = AsyncMock(return_value={"id": "variant"})
    monkeypatch.setattr(media_router, "_build_media_item_response", response_builder)
    db = _FakeDb()
    user_id = uuid.uuid4()
    upload_file = UploadFile(
        file=BytesIO(_split_image()),
        filename="profile.png",
        headers=Headers({"content-type": "image/png"}),
    )

    response = await media_router.upload_adjusted_image(
        file=upload_file,
        purpose="profile_photo",
        crop_x=0,
        crop_y=0,
        crop_width=0.5,
        crop_height=1,
        title=None,
        description=None,
        current_user=AuthUser(
            sub=str(user_id),
            email="member@example.com",
            app_metadata={"roles": ["member"]},
        ),
        db=db,
    )

    source, variant = db.items
    assert response == {"id": "variant"}
    assert variant.source_media_id == source.id
    assert source.metadata_info["presentation_original"] is True
    assert variant.metadata_info["presentation_variant"] is True
    assert db.flushed is True
    assert db.committed is True
    assert upload.await_count == 2
    assert upload.await_args_list[0].kwargs["bucket_type"] == BucketType.PRIVATE
    assert upload.await_args_list[1].kwargs["bucket_type"] == BucketType.PUBLIC


def test_preserved_original_requires_uploader_or_admin():
    owner_id = uuid.uuid4()
    source = MediaItem(
        id=uuid.uuid4(),
        media_type=MediaType.IMAGE,
        file_url="https://private.example.com/original.jpg",
        uploaded_by=owner_id,
        metadata_info={"presentation_original": True},
    )

    with pytest.raises(HTTPException, match="Media item not found"):
        media_router._require_original_access(source, None)

    owner = AuthUser(
        sub=str(owner_id),
        email="owner@example.com",
        app_metadata={"roles": ["member"]},
    )
    media_router._require_original_access(source, owner)
