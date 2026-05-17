"""SessionBooking request/response schemas.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.sessions_service.models.enums import (
    BookingChannel,
    SessionBookingStatus,
)


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
    fee_amount_kobo: int = Field(default=0, ge=0)
    notes: Optional[str] = Field(default=None, max_length=500)
    pay_with_bubbles: bool = False


class BookingConfirmRequest(BaseModel):
    """Transition a PENDING booking to CONFIRMED after payment cleared."""

    payment_intent_id: Optional[uuid.UUID] = None
    wallet_transaction_id: Optional[uuid.UUID] = None


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
