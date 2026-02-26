"""Media service albums router: album CRUD."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.media_service.models import Album, AlbumItem, MediaItem
from services.media_service.routers._helpers import (
    _build_media_item_response,
    _maybe_presign_url,
    _stable_daily_album_index,
)
from services.media_service.schemas import (
    AlbumCoverPhoto,
    AlbumCreate,
    AlbumResponse,
    AlbumUpdate,
    AlbumWithMedia,
    MediaItemResponse,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.post("/albums", response_model=AlbumResponse)
async def create_album(
    album: AlbumCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new album (admin only)."""
    db_album = Album(**album.model_dump(), created_by=current_user.user_id)
    db.add(db_album)
    await db.commit()
    await db.refresh(db_album)

    response = AlbumResponse.model_validate(db_album)
    response.media_count = 0
    return response


@router.get("/albums", response_model=List[AlbumResponse])
async def list_albums(
    album_type: Optional[str] = None,
    linked_entity_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List all albums, optionally filtered by type or linked entity."""
    query = select(Album).order_by(desc(Album.created_at))

    if album_type:
        query = query.where(Album.album_type == album_type)

    if linked_entity_id:
        query = query.where(Album.linked_entity_id == linked_entity_id)

    result = await db.execute(query)
    albums = result.scalars().all()

    if not albums:
        return []

    album_ids = [album.id for album in albums]

    # Load album media once so we can derive:
    # 1) media_count
    # 2) cover_photo (manual cover first, then stable daily fallback)
    items_result = await db.execute(
        select(
            AlbumItem.album_id.label("album_id"),
            AlbumItem.order.label("item_order"),
            MediaItem.id.label("media_id"),
            MediaItem.file_url.label("file_url"),
            MediaItem.thumbnail_url.label("thumbnail_url"),
            MediaItem.created_at.label("created_at"),
        )
        .join(MediaItem, MediaItem.id == AlbumItem.media_item_id)
        .where(AlbumItem.album_id.in_(album_ids))
        .order_by(AlbumItem.album_id, AlbumItem.order, desc(MediaItem.created_at))
    )
    item_rows = items_result.fetchall()

    media_by_album: dict[uuid.UUID, list[dict[str, object]]] = {}
    for row in item_rows:
        media_by_album.setdefault(row.album_id, []).append(
            {
                "id": row.media_id,
                "file_url": row.file_url,
                "thumbnail_url": row.thumbnail_url,
            }
        )

    response_list = []
    for album in albums:
        album_data = AlbumResponse.model_validate(album)
        album_media = media_by_album.get(album.id, [])
        album_data.media_count = len(album_media)

        selected_cover: Optional[dict[str, object]] = None
        if album.cover_media_id:
            selected_cover = next(
                (
                    media
                    for media in album_media
                    if str(media["id"]) == str(album.cover_media_id)
                ),
                None,
            )

        if not selected_cover and album_media:
            selected_cover = album_media[
                _stable_daily_album_index(album.id, len(album_media))
            ]

        if selected_cover:
            album_data.cover_photo = AlbumCoverPhoto(
                id=selected_cover["id"],
                file_url=_maybe_presign_url(selected_cover["file_url"]),
                thumbnail_url=_maybe_presign_url(selected_cover["thumbnail_url"]),
            )

        response_list.append(album_data)

    return response_list


@router.get("/albums/{album_id}", response_model=AlbumWithMedia)
async def get_album(album_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    """Get album details with all media items."""
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Get media items in album via AlbumItem
    # Join AlbumItem and MediaItem, order by AlbumItem.order
    stmt = (
        select(MediaItem)
        .join(AlbumItem, AlbumItem.media_item_id == MediaItem.id)
        .where(AlbumItem.album_id == album_id)
        .order_by(AlbumItem.order, desc(MediaItem.created_at))
    )

    media_result = await db.execute(stmt)
    media_items = media_result.scalars().all()

    # Build media responses with tags
    media_responses = []
    for item in media_items:
        media_responses.append(await _build_media_item_response(db, item))

    album_data = AlbumResponse.model_validate(album)
    album_data.media_count = len(media_items)

    return AlbumWithMedia(**album_data.model_dump(), media_items=media_responses)


@router.put("/albums/{album_id}", response_model=AlbumResponse)
@router.patch("/albums/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: uuid.UUID,
    album_update: AlbumUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update album (admin only)."""
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    update_data = album_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(album, field, value)

    await db.commit()
    await db.refresh(album)

    response = AlbumResponse.model_validate(album)
    count_query = select(func.count(AlbumItem.id)).where(AlbumItem.album_id == album.id)
    count_result = await db.execute(count_query)
    response.media_count = count_result.scalar_one()

    return response


@router.delete("/albums/{album_id}")
async def delete_album(
    album_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete album. Note: Does NOT delete the actual media items, just the album and associations."""
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Delete album (cascade will handle AlbumItems, but MediaItems remain as they might be in other albums or standalone)
    await db.delete(album)
    await db.commit()

    return {"message": "Album deleted successfully"}
