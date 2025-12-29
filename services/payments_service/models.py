import enum
import random
import string
import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


class PaymentPurpose(str, enum.Enum):
    COMMUNITY_ANNUAL = "community_annual"
    COMMUNITY_EVENT = "community_event"
    CLUB_MONTHLY = "club_monthly"
    CLUB_QUATERLY = "club_quaterly"
    CLUB_BIANNUALLY = "club_biannually"
    CLUB_ANNUALLY = "club_annually"
    CLUB_ACTIVATION = "club_activation"
    ACADEMY_COHORT = "academy_cohort"
    POOL_FEE = "pool_fee"
    RIDE_SHARE = "ride_share"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )

    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    payer_email: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    purpose: Mapped[PaymentPurpose] = mapped_column(
        SAEnum(PaymentPurpose, name="payment_purpose_enum"),
        nullable=False,
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="NGN", nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum"),
        default=PaymentStatus.PENDING,
        nullable=False,
    )

    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    entitlement_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entitlement_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "metadata" is reserved by SQLAlchemy's Declarative API, so we map the DB column
    # named "metadata" onto a safe attribute name.
    payment_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @staticmethod
    def generate_reference() -> str:
        suffix = "".join(random.choices(string.digits, k=5))
        return f"PAY-{suffix}"

    def __repr__(self):
        return f"<Payment {self.reference}>"
