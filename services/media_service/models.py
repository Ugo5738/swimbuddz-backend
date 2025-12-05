"""Media Service models for SwimBuddz Gallery & Media system."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class MediaType(str, Enum):
    """Type of media content."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"


class AlbumType(str, Enum):
    """Type of album for categorization."""

    GENERAL = "GENERAL"
    CLUB = "CLUB"
    COMMUNITY = "COMMUNITY"
    SESSION = "SESSION"
    EVENT = "EVENT"
    ACADEMY = "ACADEMY"
    PRODUCT = "PRODUCT"
    MARKETING = "MARKETING"
    USER_GENERATED = "USER_GENERATED"


class MediaItem(Base):
    """Comprehensive media item (photo, video, etc.)."""

    __tablename__ = "media_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    media_type: Mapped[MediaType] = mapped_column(
        String, nullable=False, default=MediaType.IMAGE
    )

    # Storage URLs
    file_url: Mapped[str] = mapped_column(String, nullable=False)  # Main file
    thumbnail_url: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Thumbnail/Preview

    # Metadata
    title: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    alt_text: Mapped[str] = mapped_column(String, nullable=True)

    # Technical Metadata (resolution, size, duration, format)
    metadata_info: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=True, default=dict
    )

    # Processing status (for video transcoding etc)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    album_items = relationship(
        "AlbumItem",
        back_populates="media_item",
        cascade="all, delete-orphan",
    )
    tags = relationship(
        "MediaTag",
        back_populates="media_item",
        cascade="all, delete-orphan",
    )
    site_assets = relationship(
        "SiteAsset",
        back_populates="media_item",
        cascade="all, delete-orphan",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<MediaItem {self.id} {self.media_type}>"


class Album(Base):
    """Collection of media items."""

    __tablename__ = "albums"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=True)

    album_type: Mapped[AlbumType] = mapped_column(
        String, nullable=False, default=AlbumType.GENERAL
    )

    # Decoupled linkage to other entities (Session, Event, Product, etc.)
    linked_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    linked_entity_type: Mapped[str] = mapped_column(
        String, nullable=True
    )  # e.g., "session", "product"

    # Owner entity (e.g. if a club owns it, or a specific user)
    owner_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    cover_media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_items.id"), nullable=True
    )

    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    cover_media = relationship("MediaItem", foreign_keys=[cover_media_id])
    items = relationship(
        "AlbumItem", back_populates="album", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Album {self.title}>"


class AlbumItem(Base):
    """Link table for MediaItems in an Album with ordering."""

    __tablename__ = "album_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    album_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("albums.id"), nullable=False
    )
    media_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_items.id"), nullable=False
    )

    order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    album = relationship("Album", back_populates="items")
    media_item = relationship("MediaItem", back_populates="album_items")


class SiteAsset(Base):
    """Managed assets for website UI (banners, logos, placeholders)."""

    __tablename__ = "site_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    key: Mapped[str] = mapped_column(
        String, unique=True, nullable=False
    )  # e.g. "home_hero_banner"
    description: Mapped[str] = mapped_column(String, nullable=True)

    media_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_items.id"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    media_item = relationship("MediaItem", back_populates="site_assets")


class MediaTag(Base):
    """Tags members in media items."""

    __tablename__ = "media_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_items.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Optional coordinates for tagging in image (x, y percentages)
    x_coord: Mapped[float] = mapped_column(Float, nullable=True)
    y_coord: Mapped[float] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    media_item = relationship("MediaItem", back_populates="tags")

    def __repr__(self):
        return f"<MediaTag media={self.media_item_id} member={self.member_id}>"
