"""Media service media router: media items, uploads, and tag management."""

import uuid
from json import JSONDecodeError
from typing import List, Optional
from urllib.parse import urlparse

from arq import create_pool
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from libs.auth.dependencies import get_current_user, get_optional_user, require_admin
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger
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
from services.media_service.services.image_variants import (
    PRESENTATION_IMAGE_PRESETS,
    ImageTransformRecipe,
    NormalizedCrop,
    generate_image_variant,
)
from services.media_service.services.storage import (
    BucketType,
    get_bucket_for_purpose,
    storage_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

# ── Lazy ARQ Redis pool for enqueuing video processing jobs ──
_redis_pool = None


async def _get_redis_pool():
    """Get or create the ARQ Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(get_redis_settings())
    return _redis_pool


async def _enqueue_video_processing(
    media_item_id: str, file_url: str, bucket_type_value: str
) -> None:
    """Enqueue a video transcoding job. Fails silently so uploads still succeed."""
    try:
        pool = await _get_redis_pool()
        await pool.enqueue_job(
            "task_process_video",
            media_item_id,
            file_url,
            bucket_type_value,
            _queue_name="arq:media",
        )
        logger.info("Enqueued video processing for %s", media_item_id)
    except Exception as e:
        logger.warning(
            "Failed to enqueue video processing for %s: %s", media_item_id, e
        )


def _private_s3_key(url: Optional[str]) -> Optional[str]:
    """Return the object key if this URL points at our private S3 bucket."""
    if not url or storage_service.backend != "s3":
        return None
    private_bucket = getattr(storage_service, "bucket_private", "") or ""
    if not private_bucket or private_bucket not in url:
        return None
    key = urlparse(url).path.lstrip("/")
    return key or None


def _user_has_role(user: Optional[AuthUser], role_name: str) -> bool:
    if user is None:
        return False
    if hasattr(user, "has_role") and callable(user.has_role):
        return bool(user.has_role(role_name))
    roles = getattr(user, "roles", None)
    if isinstance(roles, list):
        return role_name in roles
    app_meta = getattr(user, "app_metadata", None)
    if isinstance(app_meta, dict):
        meta_roles = app_meta.get("roles")
        if isinstance(meta_roles, list):
            return role_name in meta_roles
        if isinstance(meta_roles, str):
            return role_name == meta_roles
    return False


def _user_is_admin_or_service(user: Optional[AuthUser]) -> bool:
    if user is None:
        return False
    role = getattr(user, "role", "")
    if role == "service_role" or _user_has_role(user, "service_role"):
        return True
    if _user_has_role(user, "admin"):
        return True
    user_email = getattr(user, "email", None)
    from libs.common.config import get_settings

    settings = get_settings()
    if user_email and user_email in (getattr(settings, "ADMIN_EMAILS", None) or []):
        return True
    return False


def _media_playback_token(media_id: uuid.UUID, expiry: int) -> str:
    """Produce a short-lived, media-bound browser playback capability.

    Issued only after a signed-in caller passes _require_original_access.
    Unlike an unguarded /play redirect, knowing a UUID is not authorization.
    """
    import hashlib
    import hmac

    from libs.common.config import get_settings

    secret = get_settings().SUPABASE_JWT_SECRET.encode("utf-8")
    data = f"media-play:{media_id}:{expiry}".encode("utf-8")
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def _valid_media_playback_token(
    media_id: uuid.UUID, expiry: int | None, token: str | None
) -> bool:
    import hmac
    import time

    if not expiry or not token or expiry < int(time.time()):
        return False
    # Reject capabilities that claim a lifetime longer than 15 minutes.
    if expiry > int(time.time()) + 900:
        return False
    return hmac.compare_digest(_media_playback_token(media_id, expiry), token)


def _protected_video(item: MediaItem) -> bool:
    return (item.metadata_info or {}).get("purpose") in {
        "milestone_evidence", "club_assessment_video",
    }


def _require_original_access(
    item: MediaItem,
    current_user: Optional[AuthUser],
    is_playback_route: bool = False,
) -> None:
    """Keep original images, payment receipts and sensitive video evidence private.

    - Payment receipts and raw presentation originals are strictly limited to
      the uploader and admins.
    - Milestone evidence and club assessment videos are accessible to the student,
      coaches (who must review them), and admins. Browser playback uses a
      short-lived capability issued only by an authenticated media lookup.
    - Milestone instructional demo videos are viewable by any authenticated user
      or via the video playback route.
    """
    metadata = item.metadata_info or {}
    purpose = metadata.get("purpose")
    # The path check protects older receipts created before purpose metadata
    # was persisted. Never issue a signed private receipt URL anonymously.
    is_receipt = purpose == "payment_proof" or "/payment-proofs/" in (
        item.file_url or ""
    )
    is_presentation_original = bool(metadata.get("presentation_original"))

    restricted_purposes = {
        "payment_proof",
        "milestone_evidence",
        "milestone_video",
        "club_assessment_video",
    }
    if (
        not is_presentation_original
        and not is_receipt
        and purpose not in restricted_purposes
    ):
        return

    # Payment receipts and original presentation assets: strictly uploader or admin
    if is_receipt or is_presentation_original:
        if current_user and (
            _user_is_admin_or_service(current_user)
            or (
                getattr(current_user, "user_id", None)
                and str(item.uploaded_by) == str(current_user.user_id)
            )
        ):
            return
        raise HTTPException(status_code=404, detail="Media item not found")

    # Instructional demos are not private student submissions.
    if purpose == "milestone_video":
        if current_user or is_playback_route:
            return
        raise HTTPException(status_code=404, detail="Media item not found")

    # Milestone evidence and club assessment videos:
    # Both API inspection and browser playback must prove authorization.
    if purpose in {"milestone_evidence", "club_assessment_video"}:
        if current_user and (
            _user_is_admin_or_service(current_user)
            or _user_has_role(current_user, "coach")
            or (
                getattr(current_user, "user_id", None)
                and str(item.uploaded_by) == str(current_user.user_id)
            )
        ):
            return
         raise HTTPException(status_code=404, detail="Media item not found")

    raise HTTPException(status_code=404, detail="Media item not found")


# ── Upload size limits per purpose ──
MAX_UPLOAD_SIZES: dict[str, int] = {
    # Images: 25 MB
    "profile_photo": 25 * 1024 * 1024,
    "cover_image": 25 * 1024 * 1024,
    "content_image": 25 * 1024 * 1024,
    "category_image": 25 * 1024 * 1024,
    "collection_image": 25 * 1024 * 1024,
    "product_image": 25 * 1024 * 1024,
    "homepage_banner": 25 * 1024 * 1024,
    "homepage_community_photo": 25 * 1024 * 1024,
    # Videos: 2 GB (iPhone ProRes/4K can exceed 500MB for short clips;
    # the transcoding worker compresses to web-friendly H.264)
    "milestone_video": 2 * 1024 * 1024 * 1024,
    "club_assessment_video": 1024 * 1024 * 1024,
    "milestone_evidence": 2 * 1024 * 1024 * 1024,
    "product_video": 2 * 1024 * 1024 * 1024,
    # Challenges: example demos and proof-of-attempt videos can be long;
    # match milestone video budget. Badge images are small icons.
    "challenge_example": 2 * 1024 * 1024 * 1024,
    "challenge_proof": 2 * 1024 * 1024 * 1024,
    "badge_image": 25 * 1024 * 1024,
    # Documents: 10 MB
    "coach_document": 10 * 1024 * 1024,
    "payment_proof": 10 * 1024 * 1024,
    "size_chart": 10 * 1024 * 1024,
    # General: 50 MB
    "general": 50 * 1024 * 1024,
    # Gallery media (admin uploads): 2 GB
    "media": 2 * 1024 * 1024 * 1024,
}

_CHUNK_SIZE = 1024 * 1024  # 1 MB

STORAGE_PREFIXES: dict[str, str] = {
    "profile_photo": "profile-photos",
    "cover_image": "cover-images",
    "content_image": "content-images",
    "category_image": "category-images",
    "collection_image": "collection-images",
    "product_image": "product-images",
    "badge_image": "badge-images",
    "challenge_example": "challenge-examples",
    "homepage_banner": "homepage-banners",
    "homepage_community_photo": "homepage-community-photos",
}


async def _read_file_with_limit(file: UploadFile, purpose: str) -> bytes:
    """Read uploaded file in chunks, enforcing size limits per purpose."""
    max_size = MAX_UPLOAD_SIZES.get(purpose, 50 * 1024 * 1024)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            max_mb = max_size / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size for {purpose} is {max_mb:.0f} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _upload_file_content(
    file: UploadFile, purpose: str, storage_name: str, bucket_type: BucketType
) -> tuple[str, str | None]:
    """Validate size without retaining another full copy of a phone video."""
    content_type = file.content_type or "application/octet-stream"
    if not content_type.startswith("video/"):
        data = await _read_file_with_limit(file, purpose)
        return await storage_service.upload_media(
            data, storage_name, content_type, bucket_type=bucket_type
        )
    max_size = MAX_UPLOAD_SIZES.get(purpose, 50 * 1024 * 1024)
    # Starlette has already spooled multipart files to disk and counted bytes.
    size = file.size
    if size is None:
        size = 0
        while chunk := await file.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > max_size:
                break
    if size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size for {purpose} is {max_size // (1024 * 1024)} MB.",
        )
    await file.seek(0)
    return await storage_service.upload_video_file(
        file.file, storage_name, content_type, bucket_type=bucket_type
    )


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
        raise HTTPException(status_code=422, detail="File must be an image")
    if media_type == "VIDEO" and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=422, detail="File must be a video")

    # Upload to storage (gallery uploads go to public bucket)
    # TODO: Handle video thumbnail generation or placeholder
    file_url, thumbnail_url = await _upload_file_content(
        file,
        "media",
        f"media/{uuid.uuid4()}/{file.filename or 'upload'}",
        BucketType.PUBLIC,
    )

    # Videos are processed asynchronously by the media worker
    is_video_upload = media_type == "VIDEO"

    # Create media record
    db_media = MediaItem(
        media_type=MediaType(media_type),
        file_url=file_url,
        thumbnail_url=thumbnail_url,
        title=title,
        description=description,
        alt_text=alt_text,
        uploaded_by=current_user.user_id,
        is_processed=not is_video_upload,  # Videos start as unprocessed
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

    # Enqueue async video processing (transcode + thumbnail + metadata)
    if is_video_upload:
        await _enqueue_video_processing(
            str(db_media.id), file_url, BucketType.PUBLIC.value
        )

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
        "club_assessment_video",
        "general",
        "profile_photo",
        "cover_image",
        "content_image",
        "category_image",
        "collection_image",
        "product_image",
        "product_video",
        "size_chart",
        # Challenges (Phase 2)
        "challenge_example",
        "challenge_proof",
        "badge_image",
    }
    if purpose not in allowed_purposes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose. Must be one of: {', '.join(sorted(allowed_purposes))}",
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
    elif purpose == "club_assessment_video":
        if not is_video:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Club assessment evidence must be a video",
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
    elif purpose == "product_video":
        if not is_video:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be a video",
            )
    elif purpose == "size_chart":
        if not (is_image or is_pdf):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be a PDF or image",
            )
    elif purpose in ("challenge_example", "challenge_proof"):
        if not (is_image or is_video):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an image or video",
            )
    elif purpose == "badge_image":
        if not is_image:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an image",
            )
    # "general" allows any file type

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
        "club_assessment_video": (
            f"club-assessments/{linked_id}" if linked_id else "club-assessments"
        ),
        "profile_photo": "profile-photos",
        "cover_image": "cover-images",
        "content_image": "content-images",
        "category_image": "category-images",
        "collection_image": "collection-images",
        "product_image": "product-images",
        "product_video": "product-videos",
        "size_chart": "size-charts",
        "challenge_example": (
            f"challenge-examples/{linked_id}" if linked_id else "challenge-examples"
        ),
        "challenge_proof": (
            f"challenge-proofs/{linked_id}" if linked_id else "challenge-proofs"
        ),
        "badge_image": "badge-images",
        "general": "uploads",
    }
    storage_prefix = storage_prefixes.get(purpose, "uploads")
    storage_name = f"{storage_prefix}/{uuid.uuid4()}.{file_ext}"

    # Supabase's legacy generic upload targets a public bucket regardless of
    # purpose. Never silently publish private swimmer evidence to that bucket.
    if (
        purpose in {"milestone_evidence", "milestone_video", "club_assessment_video"}
        and storage_service.backend != "s3"
    ):
        raise HTTPException(
            status_code=503,
            detail="Private swimmer evidence upload requires configured private S3 storage",
        )

    # Determine which bucket to use based on purpose
    bucket_type = get_bucket_for_purpose(purpose)

    file_url, thumbnail_url = await _upload_file_content(
        file, purpose, storage_name, bucket_type
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
        metadata_info={"purpose": purpose, "linked_id": linked_id},
        is_processed=not is_video,  # Videos start as unprocessed
    )
    db.add(db_media)
    await db.commit()
    await db.refresh(db_media)

    # Enqueue async video processing (transcode + thumbnail + metadata)
    if is_video:
        await _enqueue_video_processing(str(db_media.id), file_url, bucket_type.value)

    return await _build_media_item_response(db, db_media)


@router.post("/uploads/adjusted-image", response_model=MediaItemResponse)
async def upload_adjusted_image(
    file: UploadFile = File(...),
    purpose: str = Form(...),
    recipe_json: Optional[str] = Form(None),
    crop_x: Optional[float] = Form(None),
    crop_y: Optional[float] = Form(None),
    crop_width: Optional[float] = Form(None),
    crop_height: Optional[float] = Form(None),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Preserve an image upload and return its purpose-sized crop variant."""
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    if purpose not in PRESENTATION_IMAGE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This media purpose does not support image adjustment",
        )
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an image",
        )

    recipe = _parse_image_recipe(
        recipe_json,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_width,
        crop_height=crop_height,
    )
    file_data = await _read_file_with_limit(file, purpose)
    generated = generate_image_variant(
        file_data,
        purpose=purpose,
        recipe=recipe,
    )

    prefix = STORAGE_PREFIXES[purpose]
    original_name = file.filename or "image"
    original_extension = _safe_image_extension(original_name, content_type)
    original_key = f"{prefix}/originals/{uuid.uuid4()}.{original_extension}"
    variant_key = f"{prefix}/variants/{uuid.uuid4()}.{generated.extension}"
    original_bucket_type = BucketType.PRIVATE
    variant_bucket_type = get_bucket_for_purpose(purpose)

    original_url: Optional[str] = None
    original_thumbnail_url: Optional[str] = None
    variant_url: Optional[str] = None
    variant_thumbnail_url: Optional[str] = None
    try:
        original_url, original_thumbnail_url = await storage_service.upload_media(
            file_data,
            original_key,
            content_type,
            bucket_type=original_bucket_type,
            preserve_filename=True,
            generate_thumbnail=False,
        )
        variant_url, variant_thumbnail_url = await storage_service.upload_media(
            generated.data,
            variant_key,
            generated.content_type,
            bucket_type=variant_bucket_type,
            preserve_filename=True,
        )

        original_id = uuid.uuid4()
        variant_id = uuid.uuid4()
        source = MediaItem(
            id=original_id,
            media_type=MediaType.IMAGE,
            file_url=original_url,
            thumbnail_url=original_thumbnail_url,
            title=f"{title or original_name} (original)",
            description=description,
            alt_text=original_name,
            metadata_info={
                "purpose": purpose,
                "presentation_original": True,
                "width": generated.source_width,
                "height": generated.source_height,
            },
            uploaded_by=current_user.user_id,
            object_key=original_key,
            bucket_type=original_bucket_type.value,
            original_filename=original_name,
            content_type=content_type,
            size_bytes=len(file_data),
            is_processed=True,
        )
        variant = MediaItem(
            id=variant_id,
            media_type=MediaType.IMAGE,
            file_url=variant_url,
            thumbnail_url=variant_thumbnail_url,
            title=title or original_name,
            description=description,
            alt_text=original_name,
            metadata_info={
                "purpose": purpose,
                "presentation_variant": True,
                "crop": recipe.crop.model_dump(),
                "transformation_recipe": recipe.model_dump(mode="json"),
                "width": generated.output_width,
                "height": generated.output_height,
            },
            uploaded_by=current_user.user_id,
            source_media_id=original_id,
            object_key=variant_key,
            bucket_type=variant_bucket_type.value,
            original_filename=original_name,
            content_type=generated.content_type,
            size_bytes=len(generated.data),
            is_processed=True,
        )
        db.add_all([source, variant])
        await db.flush()
        response = await _build_media_item_response(db, variant)
        await db.commit()
    except Exception:
        await db.rollback()
        if variant_url:
            await storage_service.delete_media(
                variant_url,
                variant_thumbnail_url,
                variant_bucket_type,
            )
        if original_url:
            await storage_service.delete_media(
                original_url,
                original_thumbnail_url,
                original_bucket_type,
            )
        raise

    return response


def _parse_image_recipe(
    recipe_json: Optional[str],
    *,
    crop_x: Optional[float],
    crop_y: Optional[float],
    crop_width: Optional[float],
    crop_height: Optional[float],
) -> ImageTransformRecipe:
    if recipe_json:
        try:
            return ImageTransformRecipe.model_validate_json(recipe_json)
        except (ValueError, JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image transformation recipe is invalid",
            ) from exc

    if None in (crop_x, crop_y, crop_width, crop_height):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image transformation recipe is required",
        )
    try:
        return ImageTransformRecipe(
            crop=NormalizedCrop(
                x=crop_x,
                y=crop_y,
                width=crop_width,
                height=crop_height,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image crop is invalid",
        ) from exc


def _safe_image_extension(filename: str, content_type: str) -> str:
    content_type_extensions = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "image/tiff": "tiff",
    }
    if content_type in content_type_extensions:
        return content_type_extensions[content_type]
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "img"
    return suffix if suffix.isalnum() and len(suffix) <= 8 else "img"


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
        "product_video",
        "size_chart",
        # Challenges (Phase 2)
        "challenge_example",
        "challenge_proof",
        "badge_image",
    }
    if purpose not in allowed_purposes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purpose. Must be one of: {', '.join(sorted(allowed_purposes))}",
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
        metadata_info={"purpose": purpose, "linked_id": linked_id},
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
    query = select(MediaItem).where(
        func.coalesce(
            MediaItem.metadata_info["presentation_original"].as_boolean(),
            False,
        ).is_(False)
    )
    query = query.where(
        func.coalesce(MediaItem.metadata_info["purpose"].astext, "").notin_(
            (
                "payment_proof",
                "milestone_evidence",
                "milestone_video",
                "club_assessment_video",
            )
        ),
        MediaItem.file_url.not_like("%/payment-proofs/%"),
    )
    query = query.order_by(desc(MediaItem.created_at))

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
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single media item by ID."""
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    _require_original_access(item, current_user, is_playback_route=False)

    result = await _build_media_item_response(db, item)
    if _protected_video(item):
        import time
        from libs.common.config import get_settings

        expires = int(time.time()) + 900
        token = _media_playback_token(item.id, expires)
        base = get_settings().PUBLIC_GATEWAY_URL.rstrip("/") if hasattr(get_settings(), "PUBLIC_GATEWAY_URL") else get_settings().GATEWAY_URL.rstrip("/")
        # Existing clients consume file_url as the video src; keep the shape
        # while replacing raw private URLs with an authorized playback link.
        result.file_url = f"{base}/api/v1/media/media/{item.id}/play?expires={expires}&token={token}"
    return result


async def _resolve_media_playback(
    media_id: uuid.UUID,
    db: AsyncSession,
    current_user: Optional[AuthUser],
    expires: int | None = None,
    token: str | None = None,
):
    """Resolve playback headers and redirect URL for a media item."""
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")

    if _protected_video(item) and not current_user:
        if not _valid_media_playback_token(item.id, expires, token):
            raise HTTPException(status_code=404, detail="Media item not found")
    else:
        _require_original_access(item, current_user, is_playback_route=True)

    private_key = _private_s3_key(item.file_url)
    if private_key:
        try:
            head = storage_service.s3_client.head_object(
                Bucket=storage_service.bucket_private,
                Key=private_key,
            )
        except Exception as exc:
            logger.warning("Could not head private media %s: %s", media_id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media file is temporarily unavailable",
            ) from exc

        if head.get("DeleteMarker"):
            raise HTTPException(status_code=404, detail="Media file not found")

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": head.get("ContentType", "application/octet-stream"),
            "Content-Length": str(head.get("ContentLength", 0)),
            "Cache-Control": "private, max-age=300",
        }

        if item.media_type == MediaType.VIDEO:
            headers["Content-Disposition"] = "inline"

        if item.media_type == MediaType.VIDEO and item.thumbnail_url:
            headers["X-Thumbnail-Url"] = item.thumbnail_url

        if item.media_type == MediaType.VIDEO and head.get("LastModified"):
            headers["Last-Modified"] = head["LastModified"].strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )

        if item.media_type == MediaType.VIDEO:
            redirect_url = storage_service.s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": storage_service.bucket_private,
                    "Key": private_key,
                    "ResponseContentDisposition": "inline",
                },
                ExpiresIn=3600,
            )
        else:
            redirect_url = _maybe_presign_url(item.file_url)

        if not redirect_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Media playback URL unavailable",
            )
        return headers, redirect_url

    redirect_url = _maybe_presign_url(item.file_url)
    if not redirect_url:
        raise HTTPException(status_code=404, detail="Media playback URL unavailable")

    return {"Cache-Control": "public, max-age=300"}, redirect_url


@router.head(
    "/media/{media_id}/play",
    operation_id="head_media_item_playback",
)
async def head_media_item_playback(
    media_id: uuid.UUID,
    expires: int | None = None,
    token: str | None = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Answer browser HEAD probes for media playback."""
    headers, _ = await _resolve_media_playback(
        media_id, db, current_user, expires=expires, token=token
    )
    return Response(status_code=200, headers=headers)


@router.get(
    "/media/{media_id}/play",
    operation_id="get_media_item_playback",
)
async def get_media_item_playback(
    media_id: uuid.UUID,
    expires: int | None = None,
    token: str | None = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return a browser-stable playback URL for media.

    Private S3 video evidence is served via presigned GET URLs, but some
    browsers issue a HEAD probe first. A raw GET presign fails that HEAD
    check with 403, so this endpoint answers HEAD itself and only redirects
    GET requests to the signed object URL.
    """
    _, redirect_url = await _resolve_media_playback(
        media_id, db, current_user, expires=expires, token=token
    )
    return RedirectResponse(url=redirect_url, status_code=307)


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
