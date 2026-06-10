"""SessionBooking request/response schemas.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

import enum
import uuid
from datetime import date as _Date
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.sessions_service.models.enums import (
    BookingChannel,
    SessionBookingStatus,
)


class GuestIntent(str, enum.Enum):
    """Why a non-member guest attends — drives approval + follow-up."""

    SOCIAL = "social"  # open-meet friend; self-serve
    TRIAL = "trial"  # prospective student sampling a class (coach-approved, D10)


class BookingGuestCreate(BaseModel):
    """A non-member swimmer to attach to a booking (bring-a-friend).

    ``full_name`` is required in the Phase 1 named-guest flow. A minor
    (``date_of_birth`` implying age < 18 at the session) must carry
    ``guardian_name`` + ``guardian_phone`` + ``waiver_accepted`` — enforced at
    booking and again at check-in. See
    docs/design/GUEST_AND_GROUP_BOOKING_DESIGN.md §5b/§9.
    """

    full_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=32)
    intent: GuestIntent = GuestIntent.SOCIAL
    date_of_birth: Optional[_Date] = None
    guardian_name: Optional[str] = Field(default=None, max_length=120)
    guardian_phone: Optional[str] = Field(default=None, max_length=32)
    waiver_accepted: bool = False


class BookingGuestResponse(BaseModel):
    """A persisted guest row (for future guest-echo / check-in views)."""

    id: uuid.UUID
    full_name: Optional[str] = None
    phone: Optional[str] = None
    intent: str
    date_of_birth: Optional[_Date] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    waiver_accepted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SessionBookingCreate(BaseModel):
    """Member self-book a session ahead of time.

    Channel defaults to MEMBER_SELF. Admin and internal/corporate routes
    set channel explicitly. Default flow: this route creates
    SessionBooking(status=PENDING) with ``expires_at = booked_at + 15 min``
    and the frontend (or payments_service webhook) calls the confirm
    endpoint once Paystack payment clears, before ``expires_at``, to
    transition status → CONFIRMED. A 5-min sweep otherwise marks the
    booking EXPIRED.

    Free-session / full-Bubbles fast path: pass ``pay_with_bubbles=True``
    AND ``fee_amount_kobo`` (zero is OK for free sessions). The endpoint
    debits the member's wallet and confirms in one transaction, returning
    a CONFIRMED booking. Mirrors the existing one-click sign-in UX.
    """

    session_id: uuid.UUID
    # Deprecated/ignored: the server now computes the fee as
    # session.pool_fee × party_size (D8). Kept for backwards-compatible clients.
    fee_amount_kobo: int = Field(default=0, ge=0)
    notes: Optional[str] = Field(default=None, max_length=500)
    pay_with_bubbles: bool = False
    # Named non-member guests the member is bringing (Phase 1). Head count =
    # 1 (the member) + len(guests). Hard-capped here; the per-session limit
    # (Session.max_guests_per_booking) is enforced server-side.
    guests: List[BookingGuestCreate] = Field(default_factory=list, max_length=20)


class BookingConfirmRequest(BaseModel):
    """Transition a PENDING booking to CONFIRMED after payment cleared."""

    payment_intent_id: Optional[uuid.UUID] = None
    wallet_transaction_id: Optional[uuid.UUID] = None


class RunningLateRequest(BaseModel):
    """Member toggles their "I'll be late" flag on a booking.

    The flag is stored as a sentinel prefix in ``booking.notes`` to avoid
    requiring a schema migration. Set ``running_late=False`` to clear it.
    """

    running_late: bool = True


class AdminWalkInRequest(BaseModel):
    """Admin creates a CONFIRMED booking for a member who showed up without
    pre-booking online (the "walk-in" case).

    Used by the admin attendance UI. The admin clicks "Mark walk-in" on a
    cohort member who paid the pool fee at the door — this creates the
    booking record so the financials reconcile. Default fee is the session's
    own ``pool_fee`` (in kobo); override is allowed for unusual cases.
    """

    member_id: uuid.UUID
    fee_amount_kobo: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=500)


class AdminPoolFeeRefundRequest(BaseModel):
    """Admin refunds a booking's per-session pool fee to the member's Bubble
    wallet — the make-up case: a learner paid the ~₦3,500 pool fee, couldn't
    attend (rain-out / excused), and is owed it back to fund their make-up.

    The server routes this through the accounted ``session_booking`` refund
    path so the ledger reverses the pool-fee revenue — never the manual
    "Adjust Bubbles" tool. ``reason`` is recorded on the booking for audit.

    ``refund_heads`` scales the refund for a multi-swimmer booking (member +
    guests): None refunds the whole pool fee; N refunds N of party_size heads
    (e.g. the guests who no-showed at a per-swimmer pool). See O3.
    """

    reason: str = Field(min_length=1, max_length=300)
    refund_heads: Optional[int] = Field(default=None, ge=1)


class BulkBookingItem(BaseModel):
    """One entry in a corporate-bulk booking payload."""

    session_id: uuid.UUID
    member_id: uuid.UUID
    member_auth_id: str = Field(min_length=1, max_length=128)
    fee_amount_kobo: int = Field(default=0, ge=0)


class BulkBookingRequest(BaseModel):
    """Service-role bulk-create for corporate-wellness orchestration."""

    corporate_program_id: uuid.UUID
    items: List[BulkBookingItem] = Field(min_length=1, max_length=500)


class SessionBookingResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    member_auth_id: str
    status: SessionBookingStatus
    channel: BookingChannel
    party_size: int
    fee_amount_kobo: int
    payment_intent_id: Optional[uuid.UUID] = None
    wallet_transaction_id: Optional[uuid.UUID] = None
    corporate_program_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    booked_at: datetime
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BulkBookingResponse(BaseModel):
    """Result of a bulk-create call."""

    created: int
    skipped: int  # (session, member) pairs that already had a booking
    bookings: List[SessionBookingResponse]


class UnpaidBookingResponse(BaseModel):
    """A confirmed booking with an outstanding pool fee.

    Returned by GET /sessions/bookings/me/unpaid so the billing UI can
    surface "you owe ₦X,XXX for Session Y" with a one-click Pay button.

    A booking lands here when fee_amount_kobo > 0 and neither a payment
    intent nor a wallet transaction is linked — typically admin-recorded
    walk-ins where the member hadn't paid online at session time.
    """

    id: uuid.UUID
    session_id: uuid.UUID
    session_title: str
    session_starts_at: datetime
    session_ends_at: datetime
    fee_amount_kobo: int
    channel: BookingChannel
    booked_at: datetime
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
