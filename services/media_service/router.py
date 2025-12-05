"""FastAPI router for Media Service."""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from libs.db.session import get_async_db
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser

from services.media_service.models import (
    Album, 
    MediaItem, 
    MediaTag, 
    AlbumItem, 
    SiteAsset,
    MediaType
)
from services.media_service.schemas import (
    AlbumCreate,
    AlbumUpdate,
    AlbumResponse,
    AlbumWithMedia,
    MediaItemUpdate,
    MediaItemResponse,
    MediaTagResponse,
    SiteAssetCreate,
    SiteAssetUpdate,
    SiteAssetResponse,
)
from services.media_service.storage import storage_service


router = APIRouter(prefix="/api/v1/media", tags=["media"])


# ===== HEALTH CHECK =====


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "media"}


# ===== ALBUM ENDPOINTS =====


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
    db: AsyncSession = Depends(get_async_db)
):
    """List all albums, optionally filtered by type or linked entity."""
    query = select(Album).order_by(desc(Album.created_at))

    if album_type:
        query = query.where(Album.album_type == album_type)
    
    if linked_entity_id:
        query = query.where(Album.linked_entity_id == linked_entity_id)

    result = await db.execute(query)
    albums = result.scalars().all()

    # Add media counts
    response_list = []
    for album in albums:
        album_data = AlbumResponse.model_validate(album)
        # Count items via AlbumItem association or direct linkage if we supported that (but we use AlbumItem now)
        # Actually, we need to decide if we strictly use AlbumItem or if MediaItem has album_id.
        # The new schema has AlbumItem for M2M, but let's check if we kept album_id on MediaItem?
        # In the new schema I defined: AlbumItem link table. MediaItem does NOT have album_id column in the new schema.
        # So we count via AlbumItem.
        count_query = select(func.count(AlbumItem.id)).where(AlbumItem.album_id == album.id)
        count_result = await db.execute(count_query)
        album_data.media_count = count_result.scalar_one()
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
        tags_query = select(MediaTag.member_id).where(MediaTag.media_item_id == item.id)
        tags_result = await db.execute(tags_query)
        tags = [tag for tag in tags_result.scalars().all()]

        # Build response manually to avoid lazy-load in Pydantic validation
        media_responses.append(
            MediaItemResponse(
                id=item.id,
                file_url=item.file_url,
                thumbnail_url=item.thumbnail_url,
                title=item.title,
                description=item.description,
                alt_text=item.alt_text,
                media_type=item.media_type.value if hasattr(item.media_type, "value") else item.media_type,
                metadata_info=item.metadata_info,
                is_processed=item.is_processed,
                uploaded_by=item.uploaded_by,
                created_at=item.created_at,
                updated_at=item.updated_at,
                tags=tags,
            )
        )

    album_data = AlbumResponse.model_validate(album)
    album_data.media_count = len(media_items)

    return AlbumWithMedia(**album_data.model_dump(), media_items=media_responses)


@router.put("/albums/{album_id}", response_model=AlbumResponse)
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


# ===== MEDIA ENDPOINTS =====


@router.post("/media", response_model=MediaItemResponse)
async def upload_media(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    alt_text: Optional[str] = Form(None),
    media_type: str = Form("IMAGE"), # IMAGE or VIDEO
    album_id: Optional[uuid.UUID] = Form(None),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload new media item."""
    # Validate file type based on media_type
    if media_type == "IMAGE" and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    if media_type == "VIDEO" and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")

    # Read file data
    file_data = await file.read()

    # Upload to storage
    # TODO: Handle video thumbnail generation or placeholder
    file_url, thumbnail_url = await storage_service.upload_media(
        file_data, file.filename or f"upload_{uuid.uuid4()}", file.content_type
    )

    # Create media record
    db_media = MediaItem(
        media_type=MediaType(media_type),
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        title=title,
        description=description,
        alt_text=alt_text,
        uploaded_by=current_user.user_id,
        is_processed=True # Assume processed for now, for video might need async job
    )
    db.add(db_media)
    await db.flush() # Get ID

    # If album_id provided, link it
    if album_id:
        # Check album exists
        album_query = select(Album).where(Album.id == album_id)
        album_result = await db.execute(album_query)
        album = album_result.scalar_one_or_none()
        
        if album:
            # Get current max order
            order_query = select(func.max(AlbumItem.order)).where(AlbumItem.album_id == album_id)
            order_result = await db.execute(order_query)
            max_order = order_result.scalar() or 0
            
            album_item = AlbumItem(
                album_id=album_id,
                media_item_id=db_media.id,
                order=max_order + 1
            )
            db.add(album_item)

    await db.commit()
    await db.refresh(db_media)

    return MediaItemResponse(
        id=db_media.id,
        file_url=db_media.file_url,
        thumbnail_url=db_media.thumbnail_url,
        title=db_media.title,
        description=db_media.description,
        alt_text=db_media.alt_text,
        media_type=db_media.media_type.value if hasattr(db_media.media_type, "value") else db_media.media_type,
        metadata_info=db_media.metadata_info,
        is_processed=db_media.is_processed,
        uploaded_by=db_media.uploaded_by,
        created_at=db_media.created_at,
        updated_at=db_media.updated_at,
        tags=[],
    )


@router.get("/media", response_model=List[MediaItemResponse])
async def list_media(
    media_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_async_db),
):
    """List all media items."""
    query = select(MediaItem).order_by(desc(MediaItem.created_at))

    if media_type:
        query = query.where(MediaItem.media_type == media_type)

    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    items = result.scalars().all()

    response_list = []
    for item in items:
        # Fetch tags to avoid lazy-load error
        tags_query = select(MediaTag.member_id).where(MediaTag.media_item_id == item.id)
        tags_result = await db.execute(tags_query)
        tags = [tag for tag in tags_result.scalars().all()]

        # Build response manually to avoid lazy-load in Pydantic validation
        response_list.append(
            MediaItemResponse(
                id=item.id,
                file_url=item.file_url,
                thumbnail_url=item.thumbnail_url,
                title=item.title,
                description=item.description,
                alt_text=item.alt_text,
                media_type=item.media_type.value if hasattr(item.media_type, "value") else item.media_type,
                metadata_info=item.metadata_info,
                is_processed=item.is_processed,
                uploaded_by=item.uploaded_by,
                created_at=item.created_at,
                updated_at=item.updated_at,
                tags=tags,
            )
        )

    return response_list


@router.put("/media/{media_id}", response_model=MediaItemResponse)
async def update_media(
    media_id: uuid.UUID,
    media_update: MediaItemUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update media metadata."""
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    update_data = media_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)

    response = MediaItemResponse.model_validate(item)
    tags_query = select(MediaTag.member_id).where(MediaTag.media_item_id == item.id)
    tags_result = await db.execute(tags_query)
    response.tags = [tag for tag in tags_result.scalars().all()]
    return response


@router.delete("/media/{media_id}")
async def delete_media(
    media_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete media item and remove from storage."""
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    # Delete from storage
    await storage_service.delete_media(item.file_url, item.thumbnail_url)

    # Delete record (cascade handles tags and album_items)
    await db.delete(item)
    await db.commit()

    return {"message": "Media deleted successfully"}


# ===== SITE ASSET ENDPOINTS =====


@router.post("/assets", response_model=SiteAssetResponse)
async def create_site_asset(
    asset: SiteAssetCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update a site asset key."""
    # Check if key exists
    query = select(SiteAsset).where(SiteAsset.key == asset.key)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Asset key already exists. Use update.")

    db_asset = SiteAsset(**asset.model_dump())
    db.add(db_asset)
    await db.commit()
    await db.refresh(db_asset)
    
    # Fetch media item for response
    media_query = select(MediaItem).where(MediaItem.id == db_asset.media_item_id)
    media_result = await db.execute(media_query)
    db_asset.media_item = media_result.scalar_one_or_none()
    
    return db_asset


@router.get("/assets", response_model=List[SiteAssetResponse])
async def list_site_assets(db: AsyncSession = Depends(get_async_db)):
    """List all site assets."""
    query = select(SiteAsset).order_by(SiteAsset.key)
    result = await db.execute(query)
    assets = result.scalars().all()
    
    # Eager load media items would be better, but for now loop
    for asset in assets:
        media_query = select(MediaItem).where(MediaItem.id == asset.media_item_id)
        media_result = await db.execute(media_query)
        asset.media_item = media_result.scalar_one_or_none()
        
    return assets


@router.get("/assets/{key}", response_model=SiteAssetResponse)
async def get_site_asset(key: str, db: AsyncSession = Depends(get_async_db)):
    """Get site asset by key."""
    query = select(SiteAsset).where(SiteAsset.key == key)
    result = await db.execute(query)
    asset = result.scalar_one_or_none()
    
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
        
    media_query = select(MediaItem).where(MediaItem.id == asset.media_item_id)
    media_result = await db.execute(media_query)
    asset.media_item = media_result.scalar_one_or_none()
    
    return asset


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
    
    media_query = select(MediaItem).where(MediaItem.id == asset.media_item_id)
    media_result = await db.execute(media_query)
    asset.media_item = media_result.scalar_one_or_none()
    
    return asset


# ===== TAG ENDPOINTS =====


@router.post("/media/{media_id}/tags", response_model=MediaTagResponse)
async def tag_member_in_media(
    media_id: uuid.UUID,
    member_id: uuid.UUID = Form(...),
    x_coord: Optional[float] = Form(None),
    y_coord: Optional[float] = Form(None),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Tag a member in a media item."""
    # Verify media exists
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    # Check if tag already exists
    existing_query = select(MediaTag).where(
        MediaTag.media_item_id == media_id, MediaTag.member_id == member_id
    )
    existing_result = await db.execute(existing_query)
    existing_tag = existing_result.scalar_one_or_none()

    if existing_tag:
        return MediaTagResponse.model_validate(existing_tag)

    # Create tag
    db_tag = MediaTag(
        media_item_id=media_id, 
        member_id=member_id,
        x_coord=x_coord,
        y_coord=y_coord
    )
    db.add(db_tag)
    await db.commit()
    await db.refresh(db_tag)

    return MediaTagResponse.model_validate(db_tag)


@router.delete("/media/{media_id}/tags/{member_id}")
async def remove_tag(
    media_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove member tag from media."""
    query = select(MediaTag).where(
        MediaTag.media_item_id == media_id, MediaTag.member_id == member_id
    )
    result = await db.execute(query)
    tag = result.scalar_one_or_none()

    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.delete(tag)
    await db.commit()

    return {"message": "Tag removed successfully"}
