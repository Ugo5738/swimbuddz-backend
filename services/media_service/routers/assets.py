"""Media service assets router: site assets, URL resolution, health check, admin cleanup."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.media_service.models import (
    Album,
    AlbumItem,
    MediaItem,
    MediaTag,
    SiteAsset,
)
from services.media_service.routers._helpers import (
    _build_site_asset_response,
    _maybe_presign_url,
)
from services.media_service.schemas import (
    SiteAssetCreate,
    SiteAssetResponse,
    SiteAssetUpdate,
)
from services.media_service.services.storage import storage_service
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/media", tags=["media"])


# ============================================================================
# HEALTH CHECK
# ============================================================================


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "media"}


# ============================================================================
# URL RESOLUTION
# ============================================================================


@router.post("/urls")
async def resolve_media_urls(
    media_ids: list[str],
    db: AsyncSession = Depends(get_async_db),
):
    """
    Batch resolve media IDs to their file URLs.

    This endpoint is designed for internal service-to-service calls.
    Other services should use this instead of direct DB queries.

    Returns a dict mapping media_id -> file_url (only for found items).
    """
    if not media_ids:
        return {}

    # Filter out empty strings and convert to UUIDs
    valid_ids = []
    for id_str in media_ids:
        if id_str and id_str.strip():
            try:
                valid_ids.append(uuid.UUID(id_str))
            except ValueError:
                continue

    if not valid_ids:
        return {}

    query = select(MediaItem.id, MediaItem.file_url).where(MediaItem.id.in_(valid_ids))
    result = await db.execute(query)
    rows = result.fetchall()

    return {str(row[0]): _maybe_presign_url(row[1]) for row in rows if row[1]}


# ============================================================================
# SITE ASSET ENDPOINTS
# ============================================================================


@router.post("/assets", response_model=SiteAssetResponse)
async def create_site_asset(
    asset: SiteAssetCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or upsert a site asset key."""
    # Check if key exists
    query = select(SiteAsset).where(SiteAsset.key == asset.key)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Treat POST as upsert to avoid client friction
        existing.media_item_id = asset.media_item_id
        existing.description = asset.description
        existing.is_active = True
        await db.commit()
        await db.refresh(existing)
        return await _build_site_asset_response(db, existing)

    db_asset = SiteAsset(**asset.model_dump())
    db.add(db_asset)
    await db.commit()
    await db.refresh(db_asset)

    return await _build_site_asset_response(db, db_asset)


@router.get("/assets", response_model=List[SiteAssetResponse])
async def list_site_assets(db: AsyncSession = Depends(get_async_db)):
    """List all site assets."""
    query = select(SiteAsset).order_by(SiteAsset.key)
    result = await db.execute(query)
    assets = result.scalars().all()

    response_list = []
    for asset in assets:
        response_list.append(await _build_site_asset_response(db, asset))

    return response_list


@router.get("/assets/{key}", response_model=SiteAssetResponse)
async def get_site_asset(key: str, db: AsyncSession = Depends(get_async_db)):
    """Get site asset by key."""
    query = select(SiteAsset).where(SiteAsset.key == key)
    result = await db.execute(query)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    return await _build_site_asset_response(db, asset)


@router.put("/assets/{key}", response_model=SiteAssetResponse)
async def update_site_asset(
    key: str,
    asset_update: SiteAssetUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update site asset."""
    query = select(SiteAsset).where(SiteAsset.key == key)
    result = await db.execute(query)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    update_data = asset_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(asset, field, value)

    await db.commit()
    await db.refresh(asset)

    return await _build_site_asset_response(db, asset)


@router.delete("/assets/{key}")
async def delete_site_asset(
    key: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete site asset and, if unused elsewhere, its media + storage objects."""
    query = select(SiteAsset).where(SiteAsset.key == key)
    result = await db.execute(query)
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    media_item_id = asset.media_item_id

    await db.delete(asset)
    await db.flush()  # ensure subsequent queries see deletion

    # Only delete media if not referenced elsewhere (other site assets or albums)
    site_asset_count_query = select(func.count(SiteAsset.id)).where(
        SiteAsset.media_item_id == media_item_id
    )
    site_asset_count_result = await db.execute(site_asset_count_query)
    site_asset_refs = site_asset_count_result.scalar_one()

    album_item_count_query = select(func.count(AlbumItem.id)).where(
        AlbumItem.media_item_id == media_item_id
    )
    album_item_count_result = await db.execute(album_item_count_query)
    album_refs = album_item_count_result.scalar_one()

    if site_asset_refs == 0 and album_refs == 0:
        media_query = select(MediaItem).where(MediaItem.id == media_item_id)
        media_result = await db.execute(media_query)
        media_item = media_result.scalar_one_or_none()
        if media_item:
            await storage_service.delete_media(
                media_item.file_url, media_item.thumbnail_url
            )
            await db.delete(media_item)

    await db.commit()
    return {"message": "Asset deleted successfully"}


# ============================================================================
# ADMIN CLEANUP
# ============================================================================


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_media(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete media artifacts for a member (Admin only).
    """
    media_ids = (
        (
            await db.execute(
                select(MediaItem.id).where(MediaItem.uploaded_by == member_id)
            )
        )
        .scalars()
        .all()
    )
    album_ids = (
        (await db.execute(select(Album.id).where(Album.created_by == member_id)))
        .scalars()
        .all()
    )

    tags_by_member = await db.execute(
        delete(MediaTag).where(MediaTag.member_id == member_id)
    )

    deleted_item_tags = 0
    deleted_items = 0
    deleted_album_items = 0
    deleted_site_assets = 0
    deleted_albums = 0

    if media_ids:
        tag_result = await db.execute(
            delete(MediaTag).where(MediaTag.media_item_id.in_(media_ids))
        )
        deleted_item_tags = tag_result.rowcount or 0

        album_item_result = await db.execute(
            delete(AlbumItem).where(AlbumItem.media_item_id.in_(media_ids))
        )
        deleted_album_items += album_item_result.rowcount or 0

        site_asset_result = await db.execute(
            delete(SiteAsset).where(SiteAsset.media_item_id.in_(media_ids))
        )
        deleted_site_assets = site_asset_result.rowcount or 0

        item_result = await db.execute(
            delete(MediaItem).where(MediaItem.id.in_(media_ids))
        )
        deleted_items = item_result.rowcount or 0

    if album_ids:
        album_item_result = await db.execute(
            delete(AlbumItem).where(AlbumItem.album_id.in_(album_ids))
        )
        deleted_album_items += album_item_result.rowcount or 0

        album_result = await db.execute(delete(Album).where(Album.id.in_(album_ids)))
        deleted_albums = album_result.rowcount or 0

    await db.commit()
    return {
        "deleted_member_tags": tags_by_member.rowcount or 0,
        "deleted_item_tags": deleted_item_tags,
        "deleted_media_items": deleted_items,
        "deleted_album_items": deleted_album_items,
        "deleted_site_assets": deleted_site_assets,
        "deleted_albums": deleted_albums,
    }
