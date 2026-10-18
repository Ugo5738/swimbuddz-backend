"""API contracts for the private session media vault."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VaultRole = Literal["viewer", "contributor", "curator", "admin"]
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
    default_session_visibility: Literal[
        "private", "selected_members", "session_participants"
    ] = "selected_members"
    attendee_can_view: bool = True
    attendee_can_contribute: bool = True
    default_allow_member_clips: bool = False
    default_allow_member_downloads: bool = False

    @model_validator(mode="after")
    def validate_dates_and_link(self):
        if self.session_id and self.event_id:
            raise ValueError("Use either session_id or event_id, not both")
        if (
            self.default_session_visibility == "session_participants"
            and not self.session_id
        ):
            raise ValueError(
                "Session participant visibility requires a linked scheduled session"
            )
        if self.default_session_visibility == "private":
            self.default_allow_member_clips = False
            self.default_allow_member_downloads = False
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
    default_session_visibility: Optional[
        Literal["private", "selected_members", "session_participants"]
    ] = None
    default_allow_member_clips: Optional[bool] = None
    default_allow_member_downloads: Optional[bool] = None
    apply_defaults_to_existing: bool = False
    attendee_can_view: Optional[bool] = None
    attendee_can_contribute: Optional[bool] = None


class VaultResponse(BaseModel):
    id: uuid.UUID
    session_id: Optional[uuid.UUID]
    event_id: Optional[uuid.UUID]
    owner_member_id: Optional[uuid.UUID] = None
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
    default_session_visibility: str = "selected_members"
    attendee_can_view: bool = True
    attendee_can_contribute: bool = True
    default_allow_member_clips: bool = False
    default_allow_member_downloads: bool = False
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


class PersonalMediaShareUpdate(BaseModel):
    share_with_community: bool


class AttendanceAccessChange(BaseModel):
    member_id: uuid.UUID
    deny: bool = True


class PersonalCollectionCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    share_with_community: bool = False
    description: Optional[str] = Field(default=None, max_length=2000)


class PersonalCollectionUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    share_with_community: Optional[bool] = None
    description: Optional[str] = Field(default=None, max_length=2000)


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


class BulkVaultGrantCreate(BaseModel):
    """Grant one member access to selected vaults or all existing vaults."""

    member_id: uuid.UUID
    role: Literal["viewer", "contributor", "curator"]
    scope: Literal["selected", "all_current"]
    vault_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    starts_at: datetime
    expires_at: datetime
    can_download_originals: bool = False

    @model_validator(mode="after")
    def validate_bulk_grant(self):
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        if self.scope == "selected" and not self.vault_ids:
            raise ValueError("Select at least one vault")
        if self.scope == "all_current" and self.vault_ids:
            raise ValueError(
                "Do not supply vault_ids when selecting all current vaults"
            )
        if len(self.vault_ids) != len(set(self.vault_ids)):
            raise ValueError("Duplicate vault IDs are not allowed")
        if self.role == "curator":
            self.can_download_originals = True
        return self


class BulkVaultGrantResponse(BaseModel):
    granted_count: int
    vault_ids: list[uuid.UUID]


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
    notification_dispatched: bool = False

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
    session_id: Optional[uuid.UUID] = None
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, values: list[str]) -> list[str]:
        labels = []
        seen = set()
        for raw in values:
            label = raw.strip()
            if not label or len(label) > 40:
                raise ValueError("Labels must be between 1 and 40 characters")
            if label.casefold() not in seen:
                labels.append(label)
                seen.add(label.casefold())
        return labels


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
    source_session_id: Optional[uuid.UUID] = None
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
    labels: list[str] = Field(default_factory=list)
    session_visibility: str = "selected_members"
    allow_member_clips: bool = False
    allow_member_downloads: bool = False
    duplicate_of_id: Optional[uuid.UUID]
    published_media_id: Optional[uuid.UUID]
    published_at: Optional[datetime]
    uploaded_by: uuid.UUID
    created_at: datetime
    preview_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    preview_status: str = "pending"

    model_config = ConfigDict(from_attributes=True)


class VaultMediaListResponse(BaseModel):
    items: list[VaultMediaResponse]
    total: int
    page: int
    page_size: int


class MediaAssignmentRequest(BaseModel):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    session_id: Optional[uuid.UUID] = None
    replace_featured: bool = False
    grant_access_to_featured: bool = False


class MediaAssignmentResponse(BaseModel):
    updated_count: int


class MemberVideoShareRequest(BaseModel):
    """Only administrators can give or revoke a member's personal media access."""

    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["grant", "revoke"] = "grant"


class MemberVideoShareResponse(BaseModel):
    affected_count: int


class MemberVideoItem(BaseModel):
    media: VaultMediaResponse
    vault_title: str
    capture_date: date
    session_id: Optional[uuid.UUID]
    session_title: Optional[str] = None
    session_starts_at: Optional[datetime] = None
    location_name: Optional[str] = None
    session_type: Optional[str] = None


class MemberVideosResponse(BaseModel):
    items: list[MemberVideoItem]
    total: int
    page: int
    page_size: int


class SessionMediaPolicyUpdate(BaseModel):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    session_visibility: Literal["private", "selected_members", "session_participants"]
    allow_member_clips: bool = False
    allow_member_downloads: bool = False


class ClipCreate(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_clip(self):
        if self.end_ms - self.start_ms < 1000:
            raise ValueError("Clip must be at least 1 second long")
        # Source duration is independently verified by ffprobe in the worker.
        # This bound avoids overflow and malformed multi-day timestamps.
        if self.end_ms > 24 * 60 * 60 * 1000:
            raise ValueError("Clip timestamps must be within 24 hours")
        return self


class ClipResponse(BaseModel):
    id: uuid.UUID
    status: str
    start_ms: int
    end_ms: int
    expires_at: datetime
    download_url: Optional[str] = None
    error_message: Optional[str] = None


class ReviewUpdate(BaseModel):
    review_status: Optional[ReviewStatus] = None
    consent_status: Optional[ConsentStatus] = None
    rating: Optional[int] = Field(default=None, ge=0, le=5)
    review_notes: Optional[str] = Field(default=None, max_length=5000)
    rejection_reason: Optional[str] = Field(default=None, max_length=2000)
    labels: Optional[list[str]] = Field(default=None, max_length=20)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, labels: Optional[list[str]]) -> Optional[list[str]]:
        if labels is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in labels:
            label = " ".join(value.strip().split())
            if not label:
                continue
            if len(label) > 40:
                raise ValueError("Each media label must be 40 characters or fewer")
            key = label.casefold()
            if key not in seen:
                normalized.append(label)
                seen.add(key)
        return normalized


class BulkReviewRequest(ReviewUpdate):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class VaultMediaDeleteRequest(BaseModel):
    media_item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    delete_from_storage: bool = False


class VaultMediaDeleteResponse(BaseModel):
    removed_count: int
    storage_deleted_count: int
    bytes_deleted: int


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
