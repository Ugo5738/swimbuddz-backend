"""Stroke Lab founding-members pre-sale.

One row = one paid founding member. Capped at FOUNDING_MEMBERS_CAP
(currently 100) via an application-level check; we don't enforce the
cap with a database constraint because it would race against
concurrent signups, but the check inside a single transaction is
sufficient at our anticipated traffic (low single-digit signups per
day during pre-sale).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


# Total founding spots up for sale. Mirrored in the founding-members
# landing copy on the frontend; keep both in sync.
FOUNDING_MEMBERS_CAP = 100

# Required payment in kobo. ₦20,000 = 2,000,000 kobo. Anything below
# this on Paystack verify is rejected.
FOUNDING_MEMBER_PRICE_KOBO = 20_000 * 100


class StrokeLabFoundingMember(Base):
    """One row per claimed founding-member spot."""

    __tablename__ = "strokelab_founding_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Supabase user UUID. Unique-indexed so a member can't accidentally
    # claim twice (Paystack double-click, network retry, etc.).
    member_auth_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )

    # Paystack transaction reference (the value Paystack's popup gives
    # the client on success). We store + verify against Paystack's
    # /transaction/verify/{reference} on receipt.
    paystack_reference: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )

    # Amount actually paid, in kobo. Stored for audit even though we
    # reject anything below the cap.
    amount_paid_kobo: Mapped[int] = mapped_column(Integer, nullable=False)

    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<StrokeLabFoundingMember member={self.member_auth_id} "
            f"ref={self.paystack_reference}>"
        )
