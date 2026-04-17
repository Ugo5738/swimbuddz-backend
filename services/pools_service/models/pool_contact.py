"""PoolContact — multiple contacts per pool with distinct roles."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.pools_service.models.enums import PoolContactRole, enum_values


class PoolContact(Base):
    """A person associated with a pool (owner, manager, front-desk, etc.)."""

    __tablename__ = "pool_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[PoolContactRole] = mapped_column(
        SAEnum(
            PoolContactRole,
            values_callable=enum_values,
            name="pool_contact_role_enum",
        ),
        nullable=False,
        default=PoolContactRole.MANAGER,
    )
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    whatsapp: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    pool: Mapped["Pool"] = relationship("Pool", back_populates="contacts")  # noqa: F821

    def __repr__(self):
        return f"<PoolContact {self.name} ({self.role.value}) for pool {self.pool_id}>"
