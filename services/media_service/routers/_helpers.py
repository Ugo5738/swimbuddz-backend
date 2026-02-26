"""Shared helper functions for media service routers."""

import hashlib
import uuid
from datetime import datetime, timezone

from services.media_service.models import MediaItem, MediaTag, SiteAsset
from services.media_service.schemas import MediaItemResponse, SiteAssetResponse
from services.media_service.services.storage import storage_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _maybe_presign_url(url: str | None) -> str | None:
    """
    If the URL points to our private S3 bucket, replace it with a presigned URL.
    Public/CloudFront/external URLs are returned as-is.
    """
    if not url:
        return url
    private_bucket = (
        storage_service.bucket_private
        if hasattr(storage_service, "bucket_private")
        else ""
    )
    if private_bucket and private_bucket in url and storage_service.backend == "s3":
        from urllib.parse import urlparse

        key = urlparse(url).path.lstrip("/")
        if key:
            try:
                return storage_service.s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": private_bucket, "Key": key},
                    ExpiresIn=3600,  # 1 hour
                )
            except Exception:
                pass  # Fall through to return raw URL
    return url


def _stable_daily_album_index(album_id: uuid.UUID, item_count: int) -> int:
    """
    Deterministic per-day selector:
    - Stable for a given album within the same UTC day.
    - Rotates once per UTC day.
    """
    if item_count <= 0:
        return 0

    day_key = datetime.now(timezone.utc).date().isoformat()
    seed = f"{album_id}:{day_key}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return int(digest, 16) % item_count


async def _build_media_item_response(
    db: AsyncSession, media_item: MediaItem
) -> MediaItemResponse:
    """Builds media item response with tags without triggering lazy loads."""
    tags_query = select(MediaTag.member_id).where(
        MediaTag.media_item_id == media_item.id
    )
    tags_result = await db.execute(tags_query)
    tags = [tag for tag in tags_result.scalars().all()]

    return MediaItemResponse(
        id=media_item.id,
        file_url=_maybe_presign_url(media_item.file_url),
        thumbnail_url=_maybe_presign_url(media_item.thumbnail_url),
        title=media_item.title,
        description=media_item.description,
        alt_text=media_item.alt_text,
        media_type=(
            media_item.media_type.value
            if hasattr(media_item.media_type, "value")
            else media_item.media_type
        ),
        metadata_info=media_item.metadata_info,
        is_processed=media_item.is_processed,
        uploaded_by=media_item.uploaded_by,
        created_at=media_item.created_at,
        updated_at=media_item.updated_at,
        tags=tags,
    )


async def _build_site_asset_response(
    db: AsyncSession, asset: SiteAsset
) -> SiteAssetResponse:
    """Builds site asset response without letting Pydantic touch lazy relationships."""
    media_response = None

    if asset.media_item_id:
        media_query = select(MediaItem).where(MediaItem.id == asset.media_item_id)
        media_result = await db.execute(media_query)
        media_item = media_result.scalar_one_or_none()

        if media_item:
            media_response = await _build_media_item_response(db, media_item)

    return SiteAssetResponse(
        id=asset.id,
        key=asset.key,
        description=asset.description,
        is_active=asset.is_active,
        media_item_id=asset.media_item_id,
        media_item=media_response,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )
