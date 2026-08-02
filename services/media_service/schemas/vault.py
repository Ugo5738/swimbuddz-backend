"""API contracts for the private session media vault."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

VaultRole = Literal["contributor", "curator", "admin"]
ReviewStatus = Literal["unreviewed", "shortlisted", "approved", "rejected", "published"]
ConsentStatus = Literal["unreviewed", "cleared", "restricted", "takedown"]
S3_MAX_OBJECT_BYTES = 5 * 1024**4


class VaultCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = None
    session_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    capture_date: date
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    timezone: str = "Africa/Lagos"
    location_name: Optional[str] = None
    upload_opens_at: datetime
    upload_closes_at: datetime
    max_file_bytes: int = Field(
        default=500 * 1024**3,
        ge=5 * 1024**2,
        le=S3_MAX_OBJECT_BYTES,
    )
    max_total_bytes: int = Field(default=2 * 1024**4, ge=5 * 1024**2)
    retention_days: int = Field(default=730, ge=1, le=3650)
    consent_notice: Optional[str] = None
    shot_checklist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates_and_link(self):
        if not self.session_id and not self.event_id:
            raise ValueError("A session_id or event_id is required")
        if self.session_id and self.event_id:
            raise ValueError("Use either session_id or event_id, not both")
        if self.upload_closes_at <= self.upload_opens_at:
            raise ValueError("upload_closes_at must be after upload_opens_at")
        return self


class VaultUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = None
    status: Optional[
        Literal["scheduled", "open", "review", "published", "archived"]
    ] = None
    location_name: Optional[str] = None
    upload_opens_at: Optional[datetime] = None
    upload_closes_at: Optional[datetime] = None
    max_file_bytes: Optional[int] = Field(
        default=None,
        ge=5 * 1024**2,
        le=S3_MAX_OBJECT_BYTES,
    )
    max_total_bytes: Optional[int] = Field(default=None, ge=5 * 1024**2)
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    consent_notice: Optional[str] = None
    opt_out_count: Optional[int] = Field(default=None, ge=0)
    shot_checklist: Optional[list[str]] = None
    settings_json: Optional[dict[str, Any]] = None


class VaultResponse(BaseModel):
    id: uuid.UUID
    session_id: Optional[uuid.UUID]
    event_id: Optional[uuid.UUID]
    title: str
    description: Optional[str]
    capture_date: date
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    timezone: str
    location_name: Optional[str]
    status: str
    upload_opens_at: datetime
    upload_closes_at: datetime
    max_file_bytes: int
    max_total_bytes: int
    used_bytes: int
    auto_transcode: bool
    retention_days: int
    consent_notice: Optional[str]
    opt_out_count: int
    shot_checklist: list[str]
    settings_json: dict[str, Any]
    published_album_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    effective_role: Optional[str] = None
    item_count: int = 0
    pending_review_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class VaultListResponse(BaseModel):
    items: list[VaultResponse]
    total: int


class VaultGrantCreate(BaseModel):
    member_id: uuid.UUID
    role: VaultRole
    starts_at: datetime
    expires_at: datetime
    can_download_originals: bool = False

    @model_validator(mode="after")
    def validate_window(self):
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        if self.role in {"curator", "admin"}:
            self.can_download_originals = True
        return self


class VaultGrantResponse(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    member_id: uuid.UUID
    role: str
    starts_at: datetime
    expires_at: datetime
    source: str
    can_download_originals: bool
    revoked_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuestLinkCreate(BaseModel):
    label: str = Field(min_length=2, max_length=160)
    expires_at: datetime
    max_total_bytes: int = Field(default=100 * 1024**3, ge=5 * 1024**2)


class GuestLinkResponse(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    label: str
    expires_at: datetime
    max_total_bytes: int
    used_bytes: int
    revoked_at: Optional[datetime]
    created_at: datetime
    upload_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class GuestVaultResponse(BaseModel):
    vault_id: uuid.UUID
    title: str
    capture_date: date
    location_name: Optional[str]
    upload_closes_at: datetime
    max_file_bytes: int
    remaining_bytes: int
    consent_notice: Optional[str]
    shot_checklist: list[str]
    link_label: str


class UploadBatchCreate(BaseModel):
    expected_files: int = Field(ge=1, le=10000)
    expected_bytes: int = Field(ge=1)
    consent_attested: bool
    consent_attestation_text: str = Field(min_length=8, max_length=2000)
    checklist_completed: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_attestation(self):
        if not self.consent_attested:
            raise ValueError("Consent attestation is required")
        return self


class UploadBatchResponse(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    uploader_member_id: Optional[uuid.UUID]
    guest_link_id: Optional[uuid.UUID]
    status: str
    expected_files: int
    expected_bytes: int
    completed_files: int
    completed_bytes: int
    consent_attested_at: datetime
    checklist_completed: list[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MultipartInitiateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(ge=1, le=S3_MAX_OBJECT_BYTES)
    captured_at: Optional[datetime] = None
    client_fingerprint: Optional[str] = Field(default=None, max_length=128)
    checksum_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)


class MultipartInitiateResponse(BaseModel):
    media_item_id: uuid.UUID
    object_key: str
    upload_id: str
    part_size: int
    part_count: int
    expires_in_seconds: int
    duplicate_of_id: Optional[uuid.UUID] = None


class MultipartSignPartsRequest(BaseModel):
    part_numbers: list[int] = Field(min_length=1, max_length=100)


class SignedUploadPart(BaseModel):
    part_number: int
    url: str


class MultipartSignPartsResponse(BaseModel):
    parts: list[SignedUploadPart]
    expires_in_seconds: int


class CompletedPart(BaseModel):
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=200)


class MultipartCompleteRequest(BaseModel):
    parts: list[CompletedPart] = Field(min_length=1, max_length=10000)


class VaultMediaResponse(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    upload_batch_id: Optional[uuid.UUID]
    media_type: str
    original_filename: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    captured_at: Optional[datetime]
    processing_status: str
    review_status: str
    consent_status: str
    rating: Optional[int]
    review_notes: Optional[str]
    rejection_reason: Optional[str]
    duplicate_of_id: Optional[uuid.UUID]
    published_media_id: Optional[uuid.UUID]
    published_at: Optional[datetime]
    uploaded_by: uuid.UUID
    created_at: datetime
    preview_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class VaultMediaListResponse(BaseModel):
    items: list[VaultMediaResponse]
    total: int
    page: int
    page_size: int


class ReviewUpdate(BaseModel):
    review_status: Optional[ReviewStatus] = None
    consent_status: Optional[ConsentStatus] = None
    rating: Optional[int] = Field(default=None, ge=0, le=5)
    review_notes: Optional[str] = Field(default=None, max_length=5000)
    rejection_reason: Optional[str] = Field(default=None, max_length=2000)


class BulkReviewRequest(ReviewUpdate):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class PublishRequest(BaseModel):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    album_title: Optional[str] = Field(default=None, max_length=255)
    make_album_public: bool = False


class DownloadAuthorizationResponse(BaseModel):
    transfer_id: uuid.UUID
    url: str
    expires_in_seconds: int
    bytes_authorized: int
    filename: str


class TransferCompletionRequest(BaseModel):
    bytes_transferred: int = Field(ge=0)
    succeeded: bool = True


class ExportCreate(BaseModel):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    preset: Literal["original", "social-square", "social-portrait"] = "original"


class ExportResponse(BaseModel):
    id: uuid.UUID
    vault_id: uuid.UUID
    requested_by: uuid.UUID
    format: str
    preset: str
    status: str
    media_item_ids: list[str]
    size_bytes: int
    error_message: Optional[str]
    expires_at: datetime
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class BandwidthBucket(BaseModel):
    month: str
    upload_bytes: int
    download_authorized_bytes: int
    download_completed_bytes: int
    download_reconciled_bytes: int
    download_pending_estimate_bytes: int
    download_effective_bytes: int


class BandwidthSummary(BaseModel):
    months: list[BandwidthBucket]
    current_month_download_bytes: int
    global_free_allowance_bytes: int = 100 * 1024**3
    allowance_remaining_bytes: int
    reconciliation_enabled: bool
    reconciliation_last_processed_at: Optional[datetime]
    measurement_note: str


class TakedownCreate(BaseModel):
    reason: str = Field(min_length=5, max_length=5000)


class TakedownResolve(BaseModel):
    status: Literal["resolved", "dismissed"]
    resolution_notes: str = Field(min_length=2, max_length=5000)


class TakedownResponse(BaseModel):
    id: uuid.UUID
    media_item_id: uuid.UUID
    requested_by: uuid.UUID
    reason: str
    status: str
    resolved_by: Optional[uuid.UUID]
    resolution_notes: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)
