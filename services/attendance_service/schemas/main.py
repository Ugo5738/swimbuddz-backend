import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from services.attendance_service.models.enums import AttendanceRole, AttendanceStatus
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
