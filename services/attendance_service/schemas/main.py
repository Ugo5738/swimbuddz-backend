import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.attendance_service.models.enums import (
    AttendanceRole,
    AttendanceStatus,
    BookingChannel,
    SessionBookingStatus,
)
from services.attendance_service.schemas.enums import RideShareOption


class AttendanceBase(BaseModel):
    status: AttendanceStatus = AttendanceStatus.PRESENT
    role: AttendanceRole = AttendanceRole.SWIMMER
    notes: Optional[str] = None
    ride_share_option: RideShareOption = RideShareOption.NONE
    needs_ride: bool = False
    can_offer_ride: bool = False
    pickup_location: Optional[str] = None


class AttendanceCreate(AttendanceBase):
    pay_with_bubbles: bool = False  # If True, debit wallet for the session pool fee


class PublicAttendanceCreate(AttendanceBase):
    member_id: uuid.UUID


class SessionSummary(BaseModel):
    """Lightweight session info embedded in attendance responses."""

    id: str
    title: str
    session_type: str
    start_time: str
    location_name: Optional[str] = None


class AttendanceResponse(AttendanceBase):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    wallet_transaction_id: Optional[uuid.UUID] = None  # Set when paid with Bubbles

    # Optional fields populated by joins
    member_name: Optional[str] = None
    member_email: Optional[str] = None

    # Optional session details (populated by enrichment, not from_attributes)
    session: Optional[SessionSummary] = None

    model_config = ConfigDict(from_attributes=True)


class StudentAttendanceSummary(BaseModel):
    """Summary of a student's attendance across all cohort sessions."""

    member_id: uuid.UUID
    member_name: str
    member_email: str
    sessions_attended: int
    sessions_total: int
    attendance_rate: float  # 0.0 to 1.0

    model_config = ConfigDict(from_attributes=True)


class CohortAttendanceSummary(BaseModel):
    """Summary of attendance for an entire cohort."""

    cohort_id: uuid.UUID
    total_sessions: int
    students: List[StudentAttendanceSummary]

    model_config = ConfigDict(from_attributes=True)


class CoachAttendanceMarkEntry(BaseModel):
    """Single entry in a coach bulk attendance mark."""

    member_id: uuid.UUID
    status: AttendanceStatus
    notes: Optional[str] = None


class CoachAttendanceMarkRequest(BaseModel):
    """Bulk attendance mark by coach for a single session.

    Default-present model: students NOT included in `entries` are treated
    as implicitly PRESENT (no row written). The coach typically only
    submits entries for exceptions: EXCUSED, ABSENT, or LATE.

    Server upserts by (session_id, member_id) so resubmitting the same
    entry overwrites the previous status (last-write-wins). To "unmark"
    a previously-recorded exception (return a student to default-present),
    submit them with status=PRESENT — the row is deleted.
    """

    entries: List[CoachAttendanceMarkEntry]


class CoachAttendanceMarkResponse(BaseModel):
    """Result of a coach bulk attendance mark."""

    session_id: uuid.UUID
    upserted: int
    deleted: int  # PRESENT entries that removed an existing exception row
    records: List[AttendanceResponse]


# ============================================================================
# SessionBooking schemas (A1 Phase 3.3)
# ============================================================================


class SessionBookingCreate(BaseModel):
    """Member self-book a session ahead of time.

    Channel defaults to MEMBER_SELF. Admin and internal/corporate routes
    set channel explicitly. Payment is handled out-of-band: the route
    creates a SessionBooking(status=PENDING) and a payment intent in
    payments_service; on Paystack webhook / Bubbles debit success the
    booking is transitioned to CONFIRMED.
    """

    session_id: uuid.UUID
    fee_amount_kobo: int = Field(default=0, ge=0)
    notes: Optional[str] = Field(default=None, max_length=500)


class AdminBookingCreate(BaseModel):
    """Admin creates a booking on behalf of a member (channel=admin)."""

    session_id: uuid.UUID
    member_id: uuid.UUID
    fee_amount_kobo: int = Field(default=0, ge=0)
    notes: Optional[str] = Field(default=None, max_length=500)


class BulkBookingItem(BaseModel):
    """One entry in a corporate-bulk booking payload."""

    session_id: uuid.UUID
    member_id: uuid.UUID
    member_auth_id: str = Field(min_length=1, max_length=128)
    fee_amount_kobo: int = Field(default=0, ge=0)


class BulkBookingRequest(BaseModel):
    """Service-role bulk-create for corporate-wellness orchestration.

    Used by sponsor onboarding flows that pre-purchase N×M (sessions ×
    members) and want every (session, member) pair to land as a
    CONFIRMED SessionBooking in one call. Caller is expected to set
    channel=CORPORATE_BULK; corporate_program_id is required so the
    bookings can be traced to the sponsor.
    """

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
