"""Media service media router: media items, uploads, and tag management."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.media_service.models import (
    Album,
    AlbumItem,
    MediaItem,
    MediaTag,
    MediaType,
)
from services.media_service.routers._helpers import (
    _build_media_item_response,
    _maybe_presign_url,
)
from services.media_service.schemas import (
    MediaItemResponse,
    MediaItemUpdate,
    MediaTagResponse,
)
from services.media_service.services.storage import (
    BucketType,
    get_bucket_for_purpose,
    storage_service,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/media", tags=["media"])


@router.post("/media", response_model=MediaItemResponse)
async def upload_media(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    alt_text: Optional[str] = Form(None),
    media_type: str = Form("IMAGE"),  # IMAGE or VIDEO
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

    # Upload to storage (gallery uploads go to public bucket)
    # TODO: Handle video thumbnail generation or placeholder
    file_url, thumbnail_url = await storage_service.upload_media(
        file_data,
        f"media/{file.filename or f'upload_{uuid.uuid4()}'}",
        file.content_type,
        bucket_type=BucketType.PUBLIC,
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
        is_processed=True,  # Assume processed for now, for video might need async job
    )
    db.add(db_media)
    await db.flush()  # Get ID

    # If album_id provided, link it
    if album_id:
        # Check album exists
        album_query = select(Album).where(Album.id == album_id)
        album_result = await db.execute(album_query)
        album = album_result.scalar_one_or_none()

        if album:
            # Get current max order
            order_query = select(func.max(AlbumItem.order)).where(
                AlbumItem.album_id == album_id
            )
            order_result = await db.execute(order_query)
            max_order = order_result.scalar() or 0

            album_item = AlbumItem(
                album_id=album_id, media_item_id=db_media.id, order=max_order + 1
            )
            db.add(album_item)

    await db.commit()
    await db.refresh(db_media)

    return MediaItemResponse(
        id=db_media.id,
        file_url=_maybe_presign_url(db_media.file_url),
        thumbnail_url=_maybe_presign_url(db_media.thumbnail_url),
        title=db_media.title,
        description=db_media.description,
        alt_text=db_media.alt_text,
        media_type=(
            db_media.media_type.value
            if hasattr(db_media.media_type, "value")
            else db_media.media_type
        ),
        metadata_info=db_media.metadata_info,
        is_processed=db_media.is_processed,
        uploaded_by=db_media.uploaded_by,
        created_at=db_media.created_at,
        updated_at=db_media.updated_at,
        tags=[],
    )


@router.post("/uploads", response_model=MediaItemResponse)
async def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form(
        ...
    ),  # "coach_document" | "payment_proof" | "milestone_evidence" | "milestone_video" | "profile_photo" | "cover_image" | "content_image" | "category_image" | "collection_image" | "product_image" | "size_chart" | "general"
    linked_id: Optional[str] = Form(
        None
    ),  # For storage path organization (e.g., payment_reference, enrollment_id)
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Generic file upload endpoint for authenticated users.

    Supports multiple purposes:
    - coach_document: Documents for coach applications (PDF, images)
    - payment_proof: Proof of payment screenshots (PDF, images)
    - milestone_evidence: Video/image evidence for milestone completion
    - milestone_video: Demo video for a milestone
    - general: General uploads

    Returns MediaItem with file_url. The calling service should store the media_id
    in its own table to track the relationship.
    """
    content_type = file.content_type or ""
    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")
    is_pdf = content_type == "application/pdf"

    # Validate file type based on purpose
    allowed_purposes = {
        "coach_document",
        "payment_proof",
        "milestone_evidence",
        "milestone_video",
        "general",
        "profile_photo",
        "cover_image",
        "content_image",
        "category_image",
        "collection_image",
        "product_image",
        "size_chart",
    }
    if purpose not in allowed_purposes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose. Must be one of: {', '.join(allowed_purposes)}",
        )

    # Different purposes have different allowed file types
    if purpose in ("coach_document", "payment_proof"):
        if not (is_image or is_pdf):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be a PDF or image",
            )
    elif purpose == "milestone_evidence":
        if not (is_image or is_video):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an image or video",
            )
    elif purpose == "milestone_video":
        if not (is_image or is_video):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an image or video",
            )
    elif purpose in (
        "profile_photo",
        "cover_image",
        "content_image",
        "category_image",
        "collection_image",
        "product_image",
    ):
        if not is_image:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an image",
            )
    elif purpose == "size_chart":
        if not (is_image or is_pdf):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be a PDF or image",
            )
    # "general" allows any file type

    file_data = await file.read()

    # Determine storage path based on purpose
    original_name = file.filename or f"upload_{uuid.uuid4()}"
    file_ext = original_name.split(".")[-1] if "." in original_name else "bin"

    storage_prefixes = {
        "coach_document": "coach-documents",
        "payment_proof": (
            f"payment-proofs/{linked_id}" if linked_id else "payment-proofs"
        ),
        "milestone_evidence": (
            f"milestone-evidence/{linked_id}" if linked_id else "milestone-evidence"
        ),
        "milestone_video": (
            f"milestone-videos/{linked_id}" if linked_id else "milestone-videos"
        ),
        "profile_photo": "profile-photos",
        "cover_image": "cover-images",
        "content_image": "content-images",
        "category_image": "category-images",
        "collection_image": "collection-images",
        "product_image": "product-images",
        "size_chart": "size-charts",
        "general": "uploads",
    }
    storage_prefix = storage_prefixes.get(purpose, "uploads")
    storage_name = f"{storage_prefix}/{uuid.uuid4()}.{file_ext}"

    # Determine which bucket to use based on purpose
    bucket_type = get_bucket_for_purpose(purpose)

    file_url, thumbnail_url = await storage_service.upload_media(
        file_data,
        storage_name,
        content_type or "application/octet-stream",
        bucket_type=bucket_type,
    )

    # Determine media type
    if is_video:
        media_type = MediaType.VIDEO
    elif is_pdf:
        media_type = MediaType.DOCUMENT
    else:
        media_type = MediaType.IMAGE

    # Auto-generate title/description if not provided
    auto_title = title or original_name
    auto_description = description
    if not auto_description:
        if purpose == "coach_document":
            auto_description = "Coach application document"
        elif purpose == "payment_proof":
            auto_description = (
                f"Proof of payment for {linked_id}" if linked_id else "Proof of payment"
            )
        elif purpose == "milestone_evidence":
            auto_description = "Milestone evidence submission"
        elif purpose == "milestone_video":
            auto_description = "Milestone demo video"

    db_media = MediaItem(
        media_type=media_type,
        file_url=file_url,
        thumbnail_url=thumbnail_url if is_image else None,
        title=auto_title,
        description=auto_description,
        alt_text=original_name,
        uploaded_by=current_user.user_id,
        is_processed=True,
    )
    db.add(db_media)
    await db.commit()
    await db.refresh(db_media)

    return await _build_media_item_response(db, db_media)


@router.post("/register-url", response_model=MediaItemResponse)
async def register_external_url(
    url: str = Form(...),
    purpose: str = Form(
        ...
    ),  # Same as upload: coach_document, milestone_evidence, etc.
    media_type: str = Form("link"),  # "image", "video", "link"
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    linked_id: Optional[str] = Form(None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Register an external URL (YouTube, image URL, etc.) as a media item.

    This allows the same media_id pattern for both uploads and external links.
    The URL is stored directly without downloading/hosting.

    Returns MediaItem with the external URL as file_url.
    """
    # Validate URL format
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL. Must start with http:// or https://",
        )

    allowed_purposes = {
        "coach_document",
        "payment_proof",
        "milestone_evidence",
        "milestone_video",
        "general",
        "profile_photo",
        "cover_image",
        "content_image",
        "category_image",
        "collection_image",
        "product_image",
        "size_chart",
    }
    if purpose not in allowed_purposes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose. Must be one of: {', '.join(allowed_purposes)}",
        )

    # Map media_type string to enum
    type_mapping = {
        "image": MediaType.IMAGE,
        "video": MediaType.VIDEO,
        "link": MediaType.DOCUMENT,
    }
    db_media_type = type_mapping.get(media_type, MediaType.DOCUMENT)

    # Auto-generate title if not provided
    auto_title = title
    if not auto_title:
        if "youtube.com" in url or "youtu.be" in url:
            auto_title = "YouTube Video"
        else:
            auto_title = f"External {media_type}"

    db_media = MediaItem(
        media_type=db_media_type,
        file_url=url,  # Store external URL directly
        thumbnail_url=None,  # No thumbnail for external URLs
        title=auto_title,
        description=description or f"{purpose} - external URL",
        alt_text=auto_title,
        uploaded_by=current_user.user_id,
        is_processed=True,
    )
    db.add(db_media)
    await db.commit()
    await db.refresh(db_media)

    return await _build_media_item_response(db, db_media)


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
        response_list.append(await _build_media_item_response(db, item))

    return response_list


@router.get("/media/{media_id}", response_model=MediaItemResponse)
async def get_media_item(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single media item by ID."""
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    return await _build_media_item_response(db, item)


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

    return await _build_media_item_response(db, item)


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


# ============================================================================
# TAG ENDPOINTS
# ============================================================================


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
        media_item_id=media_id, member_id=member_id, x_coord=x_coord, y_coord=y_coord
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
