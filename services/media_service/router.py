"""FastAPI router for Media Service."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import desc

from libs.db.session import get_db
from libs.auth.utils import get_current_user_id, require_admin_or_media_volunteer

from .models import Album, Photo, PhotoTag
from .schemas import (
    AlbumCreate, AlbumUpdate, AlbumResponse, AlbumWithPhotos,
    PhotoUpdate, PhotoResponse, 
    PhotoTagCreate, PhotoTagResponse,
    FeaturedPhotosResponse
)
from .storage import storage_service


router = APIRouter(prefix="/media", tags=["media"])


# ===== ALBUM ENDPOINTS =====

@router.post("/albums", response_model=AlbumResponse)
async def create_album(
    album: AlbumCreate,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Create a new album (admin/media volunteers only)."""
    db_album = Album(
        **album.model_dump(),
        created_by=current_user_id
    )
    db.add(db_album)
    db.commit()
    db.refresh(db_album)
    
    response = AlbumResponse.model_validate(db_album)
    response.photo_count = 0
    return response


@router.get("/albums", response_model=List[AlbumResponse])
async def list_albums(
    album_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all albums, optionally filtered by type."""
    query = db.query(Album).order_by(desc(Album.created_at))
    
    if album_type:
        query = query.filter(Album.album_type == album_type)
    
    albums = query.all()
    
    # Add photo counts
    result = []
    for album in albums:
        album_data = AlbumResponse.model_validate(album)
        album_data.photo_count = db.query(Photo).filter(Photo.album_id == album.id).count()
        result.append(album_data)
    
    return result


@router.get("/albums/{album_id}", response_model=AlbumWithPhotos)
async def get_album(
    album_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    """Get album details with all photos."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    photos = db.query(Photo).filter(Photo.album_id == album_id).order_by(Photo.created_at).all()
    
    # Build response with tags
    photo_responses = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags = db.query(PhotoTag.member_id).filter(PhotoTag.photo_id == photo.id).all()
        photo_data.tags = [tag[0] for tag in tags]
        photo_responses.append(photo_data)
    
    album_data = AlbumResponse.model_validate(album)
    album_data.photo_count = len(photos)
    
    return AlbumWithPhotos(**album_data.model_dump(), photos=photo_responses)


@router.put("/albums/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: uuid.UUID,
    album_update: AlbumUpdate,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Update album (admin/media volunteers only)."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    update_data = album_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(album, field, value)
    
    db.commit()
    db.refresh(album)
    
    response = AlbumResponse.model_validate(album)
    response.photo_count = db.query(Photo).filter(Photo.album_id == album.id).count()
    return response


@router.delete("/albums/{album_id}")
async def delete_album(
    album_id: uuid.UUID,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Delete album and all its photos (admin/media volunteers only)."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    
    # Delete all photos in album
    photos = db.query(Photo).filter(Photo.album_id == album_id).all()
    for photo in photos:
        # Delete from storage
        await storage_service.delete_photo(photo.file_url, photo.thumbnail_url)
        # Delete tags
        db.query(PhotoTag).filter(PhotoTag.photo_id == photo.id).delete()
        db.delete(photo)
    
    # Delete album
    db.delete(album)
    db.commit()
    
    return {"message": "Album deleted successfully"}


# ===== PHOTO ENDPOINTS =====

@router.post("/albums/{album_id}/photos", response_model=PhotoResponse)
async def upload_photo(
    album_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Upload photo to album (admin/media volunteers only)."""
    # Verify album exists
    album = db.query(Album).filter(Album.id == album_id).first()
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
        uploaded_by=current_user_id
    )
    db.add(db_photo)
    db.commit()
    db.refresh(db_photo)
    
    response = PhotoResponse.model_validate(db_photo)
    response.tags = []
    return response


@router.get("/photos", response_model=List[PhotoResponse])
async def list_photos(
    album_id: Optional[uuid.UUID] = None,
    is_featured: Optional[bool] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List photos with optional filters."""
    query = db.query(Photo).order_by(desc(Photo.created_at))
    
    if album_id:
        query = query.filter(Photo.album_id == album_id)
    if is_featured is not None:
        query = query.filter(Photo.is_featured == is_featured)
    
    photos = query.limit(limit).all()
    
    # Add tags to each photo
    result = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags = db.query(PhotoTag.member_id).filter(PhotoTag.photo_id == photo.id).all()
        photo_data.tags = [tag[0] for tag in tags]
        result.append(photo_data)
    
    return result


@router.get("/photos/featured", response_model=FeaturedPhotosResponse)
async def get_featured_photos(
    limit: int = 6,
    db: Session = Depends(get_db)
):
    """Get featured photos for homepage."""
    photos = db.query(Photo)\
        .filter(Photo.is_featured == True)\
        .order_by(desc(Photo.created_at))\
        .limit(limit)\
        .all()
    
    photo_responses = []
    for photo in photos:
        photo_data = PhotoResponse.model_validate(photo)
        tags = db.query(PhotoTag.member_id).filter(PhotoTag.photo_id == photo.id).all()
        photo_data.tags = [tag[0] for tag in tags]
        photo_responses.append(photo_data)
    
    return FeaturedPhotosResponse(photos=photo_responses)


@router.put("/photos/{photo_id}", response_model=PhotoResponse)
async def update_photo(
    photo_id: uuid.UUID,
    photo_update: PhotoUpdate,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Update photo metadata (admin/media volunteers only)."""
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    update_data = photo_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(photo, field, value)
    
    db.commit()
    db.refresh(photo)
    
    response = PhotoResponse.model_validate(photo)
    tags = db.query(PhotoTag.member_id).filter(PhotoTag.photo_id == photo.id).all()
    response.tags = [tag[0] for tag in tags]
    return response


@router.delete("/photos/{photo_id}")
async def delete_photo(
    photo_id: uuid.UUID,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Delete photo (admin/media volunteers only)."""
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Delete from storage
    await storage_service.delete_photo(photo.file_url, photo.thumbnail_url)
    
    # Delete tags
    db.query(PhotoTag).filter(PhotoTag.photo_id == photo_id).delete()
    
    # Delete photo record
    db.delete(photo)
    db.commit()
    
    return {"message": "Photo deleted successfully"}


# ===== TAG ENDPOINTS =====

@router.post("/photos/{photo_id}/tags", response_model=PhotoTagResponse)
async def tag_member_in_photo(
    photo_id: uuid.UUID,
    member_id: uuid.UUID = Form(...),
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Tag a member in a photo (admin/media volunteers only)."""
    # Verify photo exists
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    # Check if tag already exists
    existing_tag = db.query(PhotoTag).filter(
        PhotoTag.photo_id == photo_id,
        PhotoTag.member_id == member_id
    ).first()
    
    if existing_tag:
        return PhotoTagResponse.model_validate(existing_tag)
    
    # TODO: Check member media consent (consent_photo field in members table)
    # For now, we'll create the tag and let the frontend handle consent warnings
    
    # Create tag
    db_tag = PhotoTag(
        photo_id=photo_id,
        member_id=member_id
    )
    db.add(db_tag)
    db.commit()
    db.refresh(db_tag)
    
    return PhotoTagResponse.model_validate(db_tag)


@router.delete("/photos/{photo_id}/tags/{member_id}")
async def remove_tag(
    photo_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user_id: uuid.UUID = Depends(require_admin_or_media_volunteer),
    db: Session = Depends(get_db)
):
    """Remove member tag from photo (admin/media volunteers only)."""
    tag = db.query(PhotoTag).filter(
        PhotoTag.photo_id == photo_id,
        PhotoTag.member_id == member_id
    ).first()
    
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    db.delete(tag)
    db.commit()
    
    return {"message": "Tag removed successfully"}
