"""Durable Academy tuition credits backed by a verified source, never cash-in."""
import uuid
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.db.base import Base
from libs.common.datetime_utils import utc_now


class AcademyFinancialCredit(Base):
    __tablename__ = "academy_financial_credits"
    __table_args__ = (
        UniqueConstraint("source_reference", name="uq_academy_financial_credit_source"),
        CheckConstraint("amount_kobo > 0", name="ck_academy_financial_credit_positive"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=False, index=True)
    source_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=True)
    source_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
