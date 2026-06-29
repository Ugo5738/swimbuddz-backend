"""Pydantic schemas for Media Service."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ===== ALBUM SCHEMAS =====
class AlbumBase(BaseModel):
    """Base album schema."""

    title: str
    description: Optional[str] = None
    album_type: (
        str  # GENERAL, SESSION, EVENT, ACADEMY, PRODUCT, MARKETING, USER_GENERATED
    )
    linked_entity_id: Optional[uuid.UUID] = None
    linked_entity_type: Optional[str] = None
    owner_entity_id: Optional[uuid.UUID] = None
    is_public: bool = True
    slug: Optional[str] = None


class AlbumCreate(AlbumBase):
    """Schema for creating an album."""

    pass


class AlbumUpdate(BaseModel):
    """Schema for updating an album."""

    title: Optional[str] = None
    description: Optional[str] = None
    album_type: Optional[str] = None
    linked_entity_id: Optional[uuid.UUID] = None
    linked_entity_type: Optional[str] = None
    cover_media_id: Optional[uuid.UUID] = None
    is_public: Optional[bool] = None
    slug: Optional[str] = None


class AlbumCoverPhoto(BaseModel):
    """Lightweight album cover photo payload for album cards."""

    id: uuid.UUID
    file_url: str
    thumbnail_url: Optional[str] = None


class AlbumResponse(AlbumBase):
    """Album response schema."""

    id: uuid.UUID
    cover_media_id: Optional[uuid.UUID] = None
    cover_photo: Optional[AlbumCoverPhoto] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    media_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


# ===== MEDIA ITEM SCHEMAS =====
class MediaItemBase(BaseModel):
    """Base media item schema."""

    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    media_type: str = "IMAGE"  # IMAGE, VIDEO, DOCUMENT


class MediaItemUpload(MediaItemBase):
    """Schema for media upload (file handled separately)."""

    album_id: Optional[uuid.UUID] = None  # Optional, can upload without album initially


class MediaItemUpdate(BaseModel):
    """Schema for updating media metadata."""

    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    is_processed: Optional[bool] = None


class MediaItemResponse(MediaItemBase):
    """Media item response schema."""

    id: uuid.UUID
    file_url: str
    thumbnail_url: Optional[str] = None
    metadata_info: Optional[Dict[str, Any]] = None
    is_processed: bool
    uploaded_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    tags: Optional[List[uuid.UUID]] = []  # List of tagged member IDs

    model_config = ConfigDict(from_attributes=True)


# ===== SITE ASSET SCHEMAS =====
class SiteAssetBase(BaseModel):
    """Base site asset schema."""

    key: str
    description: Optional[str] = None
    is_active: bool = True


class SiteAssetCreate(SiteAssetBase):
    """Schema for creating a site asset."""

    media_item_id: uuid.UUID


class SiteAssetUpdate(BaseModel):
    """Schema for updating a site asset."""

    description: Optional[str] = None
    is_active: Optional[bool] = None
    media_item_id: Optional[uuid.UUID] = None


class SiteAssetResponse(SiteAssetBase):
    """Site asset response schema."""

    id: uuid.UUID
    media_item_id: uuid.UUID
    media_item: Optional[MediaItemResponse] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== TAG SCHEMAS =====
class MediaTagCreate(BaseModel):
    """Schema for creating a media tag."""

    media_item_id: uuid.UUID
    member_id: uuid.UUID
    x_coord: Optional[float] = None
    y_coord: Optional[float] = None


class MediaTagResponse(BaseModel):
    """Media tag response schema."""

    id: uuid.UUID
    media_item_id: uuid.UUID
    member_id: uuid.UUID
    x_coord: Optional[float] = None
    y_coord: Optional[float] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== GALLERY VIEW SCHEMAS =====
class AlbumWithMedia(AlbumResponse):
    """Album with media items list."""

    media_items: List[MediaItemResponse] = []


# ===== AUDIO TRACK SCHEMAS =====
class AudioTrackBase(BaseModel):
    """Base audio track schema."""

    title: str
    artist: Optional[str] = None
    genre: Optional[str] = None
    license_type: str = "ROYALTY_FREE"


class AudioTrackCreate(AudioTrackBase):
    """Schema for creating an audio track (file handled separately)."""

    pass


class AudioTrackResponse(AudioTrackBase):
    """Audio track response schema."""

    id: uuid.UUID
    file_url: str
    duration_seconds: Optional[float] = None
    is_active: bool
    uploaded_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AudioTrackUpdate(BaseModel):
    """Schema for updating audio track metadata."""

    title: Optional[str] = None
    artist: Optional[str] = None
    genre: Optional[str] = None
    license_type: Optional[str] = None
    is_active: Optional[bool] = None


class ApplyAudioRequest(BaseModel):
    """Request body for applying audio to a video."""

    audio_track_id: uuid.UUID
    volume_mix: float = 1.0  # 0.0 = mute original, 1.0 = full replacement
    start_offset_seconds: float = 0.0  # Where in the audio track to start


# ── Admin evidence-gallery schemas ──────────────────────────────────────


class AdminEvidenceItemResponse(BaseModel):
    """A single tile in the admin evidence gallery for an enrollment.

    Composed by joining a ``StudentProgress`` row (fetched from the
    academy service) with its ``MediaItem`` (looked up locally). The
    URLs are presigned where applicable, matching the existing coach
    view's behaviour — admins see the same view, plus a download
    affordance.
    """

    # Media identity / playback
    media_id: uuid.UUID
    media_type: str  # "IMAGE" | "VIDEO" | "DOCUMENT"
    file_url: Optional[str] = None  # Presigned for private bucket
    thumbnail_url: Optional[str] = None  # Presigned for private bucket
    is_processed: bool = True
    media_created_at: datetime

    # Academy context — denormalised from the StudentProgress row so
    # the UI can render a tile without a second round-trip per item.
    enrollment_id: uuid.UUID
    milestone_id: uuid.UUID
    progress_id: uuid.UUID
    progress_status: str  # "pending" | "achieved"
    student_notes: Optional[str] = None
    claim_achieved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AdminEvidenceListResponse(BaseModel):
    """Wrapper for the admin evidence-gallery list endpoint.

    A wrapper rather than a bare list lets future pagination /
    aggregate fields (total uploaded bytes, average review age, …)
    land without a breaking change.
    """

    items: list[AdminEvidenceItemResponse]
    enrollment_id: uuid.UUID
    total: int


class MediaDownloadResponse(BaseModel):
    """Response from the admin download endpoint.

    The URL is short-lived (60 seconds by design — see
    ACADEMY_ADMIN_CONTROLS_DESIGN §9.3) and intended to be handed
    straight to the browser. ``expires_at`` is included so the
    client can show a "this link expires" hint and / or retry on
    expiry.
    """

    download_url: str
    expires_at: datetime


# ===== INTERNAL MEDIA OBJECT SCHEMAS =====


class InternalDirectUploadCreateRequest(BaseModel):
    """Service-to-service request for a browser direct-upload target."""

    purpose: str
    filename: str = "upload.bin"
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)
    linked_id: Optional[str] = None
    expires_in: int = Field(default=900, ge=60, le=3600)


class InternalDirectUploadCreateResponse(BaseModel):
    """Presigned PUT target plus the opaque media object key."""

    object_key: str
    bucket_type: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str]
    expires_in: int


class InternalObjectVerifyRequest(BaseModel):
    object_key: str
    bucket_type: str = "private"


class InternalObjectMetadataResponse(BaseModel):
    object_key: str
    bucket_type: str
    size_bytes: int
    content_type: Optional[str] = None
    etag: Optional[str] = None


class InternalObjectSignRequest(BaseModel):
    object_key: str
    bucket_type: str = "private"
    expires_in: int = Field(default=3600, ge=60, le=86400)


class InternalObjectSignResponse(BaseModel):
    url: str
    expires_in: int


class InternalObjectUploadRequest(BaseModel):
    """Service-to-service small-object upload.

    Large browser uploads should use InternalDirectUploadCreateRequest instead.
    """

    purpose: str
    filename: str = "upload.bin"
    content_type: str = "application/octet-stream"
    data_base64: str
    linked_id: Optional[str] = None


class InternalObjectUploadResponse(BaseModel):
    object_key: str
    bucket_type: str
    url: str
