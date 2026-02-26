"""Store inventory models: stock tracking and audit trail."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.store_service.models.enums import InventoryMovementType, enum_values
from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ============================================================================
# INVENTORY MODELS
# ============================================================================


class InventoryItem(Base):
    """Inventory tracking per variant."""

    __tablename__ = "store_inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Stock levels
    quantity_on_hand: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    quantity_reserved: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )  # Held in active carts

    # Thresholds
    low_stock_threshold: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5"
    )

    # Tracking
    last_restock_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sold_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="positive_stock"),
        CheckConstraint(
            "quantity_reserved >= 0 AND quantity_reserved <= quantity_on_hand",
            name="valid_reserved",
        ),
    )

    # Relationships
    variant = relationship("ProductVariant", back_populates="inventory_item")
    movements = relationship(
        "InventoryMovement",
        back_populates="inventory_item",
        cascade="all, delete-orphan",
    )

    @property
    def quantity_available(self) -> int:
        """Available quantity (on hand minus reserved)."""
        return self.quantity_on_hand - self.quantity_reserved

    def __repr__(self):
        return f"<InventoryItem variant={self.variant_id} qty={self.quantity_on_hand}>"


class InventoryMovement(Base):
    """Audit trail for inventory changes."""

    __tablename__ = "store_inventory_movements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )

    movement_type: Mapped[InventoryMovementType] = mapped_column(
        SAEnum(
            InventoryMovementType,
            values_callable=enum_values,
            name="store_inventory_movement_type_enum",
        ),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Positive = add, negative = subtract

    reference_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # order, cart, manual
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    performed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    inventory_item = relationship("InventoryItem", back_populates="movements")

    def __repr__(self):
        return f"<InventoryMovement {self.movement_type} qty={self.quantity}>"
