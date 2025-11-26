"""FastAPI router for Media Service."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from libs.db.session import get_async_db
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser

from services.media_service.models import Album, Photo, PhotoTag
from services.media_service.schemas import (
    AlbumCreate, AlbumUpdate, AlbumResponse, AlbumWithPhotos,
    PhotoUpdate, PhotoResponse, 
    PhotoTagCreate, PhotoTagResponse,
    FeaturedPhotosResponse
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
    db: AsyncSession = Depends(get_async_db)
):
    """Create a new album (admin only)."""
    db_album = Album(**album.model_dump(), created_by=current_user.user_id)
    db.add(db_album)
    await db.commit()
    await db.refresh(db_album)
    
    response = AlbumResponse.model_validate(db_album)
    response.photo_count = 0
    return response


@router.get("/albums", response_model=List[AlbumResponse])
async def list_albums(
    album_type: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db)
):
    """List all albums, optionally filtered by type."""
    query = select(Album).order_by(desc(Album.created_at))
    
    if album_type:
        query = query.where(Album.album_type == album_type)
    
    result = await db.execute(query)
    albums = result.scalars().all()
    
    # Add photo counts
    response_list = []
    for album in albums:
        album_data = AlbumResponse.model_validate(album)
        count_query = select(func.count(Photo.id)).where(Photo.album_id == album.id)
        count_result = await db.execute(count_query)
        album_data.photo_count = count_result.scalar_one()
        response_list.append(album_data)
    
    return response_list


@router.get("/albums/{album_id}", response_model=AlbumWithPhotos)
async def get_album(
    album_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db)
):
    """Get album details with all photos."""
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()
    
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    # Get photos in album
    photos_query = select(Photo).where(Photo.album_id == album_id).order_by(Photo.created_at)
    photos_result = await db.execute(photos_query)
    photos = photos_result.scalars().all()
    
    # Build photo responses with tags
    photo_responses = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags_query = select(PhotoTag.member_id).where(PhotoTag.photo_id == photo.id)
        tags_result = await db.execute(tags_query)
        photo_data.tags = [str(tag) for tag in tags_result.scalars().all()]
        photo_responses.append(photo_data)
    
    album_data = AlbumResponse.model_validate(album)
    album_data.photo_count = len(photos)
    
    return AlbumWithPhotos(**album_data.model_dump(), photos=photo_responses)


@router.put("/albums/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: uuid.UUID,
    album_update: AlbumUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
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
    count_query = select(func.count(Photo.id)).where(Photo.album_id == album.id)
    count_result = await db.execute(count_query)
    response.photo_count = count_result.scalar_one()
    
    return response


@router.delete("/albums/{album_id}")
async def delete_album(
    album_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete album and all its photos (admin only)."""
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()
    
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    # Delete all photos in album
    photos_query = select(Photo).where(Photo.album_id == album_id)
    photos_result = await db.execute(photos_query)
    photos = photos_result.scalars().all()
    
    for photo in photos:
        # Delete from storage
        await storage_service.delete_photo(photo.file_url, photo.thumbnail_url)
        
        # Delete tags
        tags_query = select(PhotoTag).where(PhotoTag.photo_id == photo.id)
        tags_result = await db.execute(tags_query)
        tags = tags_result.scalars().all()
        for tag in tags:
            await db.delete(tag)
        
        await db.delete(photo)
    
    # Delete album
    await db.delete(album)
    await db.commit()
    
    return {"message": "Album deleted successfully"}


# ===== PHOTO ENDPOINTS =====

@router.post("/albums/{album_id}/photos", response_model=PhotoResponse)
async def upload_photo(
    album_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Upload photo to album (admin only)."""
    # Verify album exists
    query = select(Album).where(Album.id == album_id)
    result = await db.execute(query)
    album = result.scalar_one_or_none()
    
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read file data
    file_data = await file.read()
    
    # Upload to storage
    file_url, thumbnail_url = await storage_service.upload_photo(
        file_data,
        file.filename or "photo.jpg",
        file.content_type
    )
    
    # Create photo record
    db_photo = Photo(
        album_id=album_id,
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        caption=caption,
        uploaded_by=current_user.user_id
    )
    db.add(db_photo)
    await db.commit()
    await db.refresh(db_photo)
    
    response = PhotoResponse.model_validate(db_photo)
    response.tags = []
    return response


@router.get("/photos", response_model=List[PhotoResponse])
async def list_photos(
    album_id: Optional[uuid.UUID] = None,
    is_featured: Optional[bool] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db)
):
    """List photos with optional filters."""
    query = select(Photo).order_by(desc(Photo.created_at))
    
    if album_id:
        query = query.where(Photo.album_id == album_id)
    if is_featured is not None:
        query = query.where(Photo.is_featured == is_featured)
    
    query = query.limit(limit)
    
    result = await db.execute(query)
    photos = result.scalars().all()
    
    # Add tags to each photo
    response_list = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags_query = select(PhotoTag.member_id).where(PhotoTag.photo_id == photo.id)
        tags_result = await db.execute(tags_query)
        photo_data.tags = [str(tag) for tag in tags_result.scalars().all()]
        response_list.append(photo_data)
    
    return response_list


@router.get("/photos/featured", response_model=FeaturedPhotosResponse)
async def get_featured_photos(
    limit: int = 6,
    db: AsyncSession = Depends(get_async_db)
):
    """Get featured photos for homepage."""
    query = select(Photo)\
        .where(Photo.is_featured == True)\
        .order_by(desc(Photo.created_at))\
        .limit(limit)
    
    result = await db.execute(query)
    photos = result.scalars().all()
    
    photo_responses = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags_query = select(PhotoTag.member_id).where(PhotoTag.photo_id == photo.id)
        tags_result = await db.execute(tags_query)
        photo_data.tags = [str(tag) for tag in tags_result.scalars().all()]
        photo_responses.append(photo_data)
    
    return FeaturedPhotosResponse(photos=photo_responses)


@router.put("/photos/{photo_id}", response_model=PhotoResponse)
async def update_photo(
    photo_id: uuid.UUID,
    photo_update: PhotoUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Update photo metadata (admin only)."""
    query = select(Photo).where(Photo.id == photo_id)
    result = await db.execute(query)
    photo = result.scalar_one_or_none()
    
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    update_data = photo_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(photo, field, value)
    
    await db.commit()
    await db.refresh(photo)
    
    response = PhotoResponse.model_validate(photo)
    tags_query = select(PhotoTag.member_id).where(PhotoTag.photo_id == photo.id)
    tags_result = await db.execute(tags_query)
    response.tags = [str(tag) for tag in tags_result.scalars().all()]
    return response


@router.delete("/photos/{photo_id}")
async def delete_photo(
    photo_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete photo (admin only)."""
    query = select(Photo).where(Photo.id == photo_id)
    result = await db.execute(query)
    photo = result.scalar_one_or_none()
    
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Delete from storage
    await storage_service.delete_photo(photo.file_url, photo.thumbnail_url)
    
    # Delete tags
    tags_query = select(PhotoTag).where(PhotoTag.photo_id == photo_id)
    tags_result = await db.execute(tags_query)
    tags = tags_result.scalars().all()
    for tag in tags:
        await db.delete(tag)
    
    # Delete photo record
    await db.delete(photo)
    await db.commit()
    
    return {"message": "Photo deleted successfully"}


# ===== TAG ENDPOINTS =====

@router.post("/photos/{photo_id}/tags", response_model=PhotoTagResponse)
async def tag_member_in_photo(
    photo_id: uuid.UUID,
    member_id: uuid.UUID = Form(...),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Tag a member in a photo (admin only)."""
    # Verify photo exists
    query = select(Photo).where(Photo.id == photo_id)
    result = await db.execute(query)
    photo = result.scalar_one_or_none()
    
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if tag already exists
    existing_query = select(PhotoTag).where(
        PhotoTag.photo_id == photo_id,
        PhotoTag.member_id == member_id
    )
    existing_result = await db.execute(existing_query)
    existing_tag = existing_result.scalar_one_or_none()
    
    if existing_tag:
        return PhotoTagResponse.model_validate(existing_tag)
    
    # TODO: Check member media consent (consent_photo field in members table)
    
    # Create tag
    db_tag = PhotoTag(
        photo_id=photo_id,
        member_id=member_id
    )
    db.add(db_tag)
    await db.commit()
    await db.refresh(db_tag)
    
    return PhotoTagResponse.model_validate(db_tag)


@router.delete("/photos/{photo_id}/tags/{member_id}")
async def remove_tag(
    photo_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """Remove member tag from photo (admin only)."""
    query = select(PhotoTag).where(
        PhotoTag.photo_id == photo_id,
        PhotoTag.member_id == member_id
    )
    result = await db.execute(query)
    tag = result.scalar_one_or_none()
    
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    await db.delete(tag)
    await db.commit()
    
    return {"message": "Tag removed successfully"}
