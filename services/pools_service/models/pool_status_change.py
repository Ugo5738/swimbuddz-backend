"""PoolStatusChange — timeline of partnership_status transitions."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.pools_service.models.enums import PartnershipStatus, enum_values


class PoolStatusChange(Base):
    """A record of a partnership status transition.

    Auto-created whenever a pool's partnership_status changes so the team
    can see how/when a pool moved between stages.
    """

    __tablename__ = "pool_status_changes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    from_status: Mapped[Optional[PartnershipStatus]] = mapped_column(
        SAEnum(
            PartnershipStatus,
            values_callable=enum_values,
            name="pool_partnership_status_enum",
            create_type=False,  # reuse existing enum type
        ),
        nullable=True,
    )
    to_status: Mapped[PartnershipStatus] = mapped_column(
        SAEnum(
            PartnershipStatus,
            values_callable=enum_values,
            name="pool_partnership_status_enum",
            create_type=False,
        ),
        nullable=False,
    )

    changed_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    pool: Mapped["Pool"] = relationship("Pool", back_populates="status_changes")  # noqa: F821

    def __repr__(self):
        f = self.from_status.value if self.from_status else "∅"
        return f"<PoolStatusChange {self.pool_id}: {f} → {self.to_status.value}>"
