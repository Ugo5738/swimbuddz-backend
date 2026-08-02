"""Private session media-vault models.

The vault is deliberately separate from the public Album model:

* originals always land in private object storage;
* access is scoped to a session/event and expires;
* review/publish is an explicit boundary;
* every transfer is auditable and attributable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class MediaVault(Base):
    """A private capture/review workspace linked to one session or event."""

    __tablename__ = "media_vaults"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_media_vaults_session_id"),
        UniqueConstraint("event_id", name="uq_media_vaults_event_id"),
        Index("ix_media_vaults_capture_date_status", "capture_date", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capture_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timezone: Mapped[str] = mapped_column(
        String(80), nullable=False, default="Africa/Lagos"
    )
    location_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scheduled", index=True
    )
    upload_opens_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    upload_closes_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    max_file_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=500 * 1024**3
    )
    max_total_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=2 * 1024**4
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    auto_transcode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=730)
    consent_notice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opt_out_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shot_checklist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    published_album_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    grants = relationship(
        "MediaVaultGrant", back_populates="vault", cascade="all, delete-orphan"
    )
    batches = relationship(
        "MediaUploadBatch", back_populates="vault", cascade="all, delete-orphan"
    )
    media_items = relationship("MediaItem", back_populates="vault")
    exports = relationship(
        "MediaVaultExport", back_populates="vault", cascade="all, delete-orphan"
    )
    guest_links = relationship(
        "MediaVaultGuestLink", back_populates="vault", cascade="all, delete-orphan"
    )


class MediaVaultGrant(Base):
    """Explicit, expiring access grant for a SwimBuddz member."""

    __tablename__ = "media_vault_grants"
    __table_args__ = (
        UniqueConstraint(
            "vault_id", "member_id", "role", name="uq_media_vault_grant_role"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vaults.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_reference_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    can_download_originals: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    vault = relationship("MediaVault", back_populates="grants")


class MediaVaultGuestLink(Base):
    """Hashed capability link for a short-lived external contributor."""

    __tablename__ = "media_vault_guest_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vaults.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    max_total_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=100 * 1024**3
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    vault = relationship("MediaVault", back_populates="guest_links")


class MediaUploadBatch(Base):
    """A contributor's resumable group of uploads."""

    __tablename__ = "media_upload_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vaults.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploader_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    uploader_auth_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    guest_link_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vault_guest_links.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", index=True
    )
    expected_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    consent_attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consent_attestation_text: Mapped[str] = mapped_column(Text, nullable=False)
    checklist_completed: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    vault = relationship("MediaVault", back_populates="batches")
    guest_link = relationship("MediaVaultGuestLink")
    media_items = relationship("MediaItem", back_populates="upload_batch")


class MediaVaultExport(Base):
    """Asynchronous manifest/ZIP export of selected full-quality originals."""

    __tablename__ = "media_vault_exports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vaults.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    format: Mapped[str] = mapped_column(String(24), nullable=False, default="zip")
    preset: Mapped[str] = mapped_column(String(32), nullable=False, default="original")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", index=True
    )
    media_item_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    object_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    vault = relationship("MediaVault", back_populates="exports")


class MediaTransferLog(Base):
    """Append-only application transfer ledger.

    ``bytes_authorized`` is exact for the object/range signed. ``bytes_transferred``
    is populated by client completion callbacks or an access-log ingestion job,
    and ``measurement_source`` says how trustworthy that number is.
    """

    __tablename__ = "media_transfer_logs"
    __table_args__ = (
        Index("ix_media_transfer_logs_month_direction", "created_at", "direction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vault_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vaults.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    media_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    export_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_vault_exports.id", ondelete="SET NULL"),
        nullable=True,
    )
    object_key: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    actor_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    transfer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    delivery_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="authorized", index=True
    )
    bytes_authorized: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_transferred: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    measurement_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="authorized"
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MediaAccessLogObject(Base):
    """One S3 server-access-log object consumed by the reconciler."""

    __tablename__ = "media_access_log_objects"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "source_bucket",
            "object_key",
            name="uq_media_access_log_object_source",
        ),
        Index("ix_media_access_log_objects_processed_at", "processed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="s3")
    source_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="processing", index=True
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MediaAccessLogEvent(Base):
    """An actual object response reported by AWS access logging."""

    __tablename__ = "media_access_log_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "target_bucket",
            "request_id",
            name="uq_media_access_log_event_request",
        ),
        Index(
            "ix_media_access_log_events_occurred_match",
            "occurred_at",
            "match_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_log_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_access_log_objects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transfer_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_transfer_logs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="s3")
    target_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    match_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unmatched", index=True
    )
    remote_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MediaTakedownRequest(Base):
    """Member/admin request to restrict or remove a vault/public item."""

    __tablename__ = "media_takedown_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", index=True
    )
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
