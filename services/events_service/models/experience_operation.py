"""Fences delayed Experience binding requests after compensation."""

import uuid
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.db.base import Base


class ExperienceBindingOperation(Base):
    __tablename__ = "experience_binding_operations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    offering_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    event_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
