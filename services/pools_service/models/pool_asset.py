"""PoolAsset — photos, documents, videos associated with a pool."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.pools_service.models.enums import PoolAssetType, enum_values


class PoolAsset(Base):
    """An asset (photo, document, video) associated with a pool.

    Stores references to files rather than the files themselves. For photos,
    we store either a direct URL or a media_service media_id. Captions and
    display_order let admins curate a gallery.
    """

    __tablename__ = "pool_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    asset_type: Mapped[PoolAssetType] = mapped_column(
        SAEnum(
            PoolAssetType,
            values_callable=enum_values,
            name="pool_asset_type_enum",
        ),
        nullable=False,
        default=PoolAssetType.PHOTO,
    )

    # Either media_service id OR direct URL (at least one should be set)
    media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    uploaded_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    pool: Mapped["Pool"] = relationship("Pool", back_populates="assets")  # noqa: F821

    def __repr__(self):
        return f"<PoolAsset {self.asset_type.value} for pool {self.pool_id}>"
