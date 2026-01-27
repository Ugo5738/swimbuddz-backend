import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from services.transport_service.models import RideShareOption


class AttendanceBase(BaseModel):
    status: str = "PRESENT"
    role: str = "SWIMMER"
    notes: Optional[str] = None
    ride_share_option: RideShareOption = RideShareOption.NONE
    needs_ride: bool = False
    can_offer_ride: bool = False
    pickup_location: Optional[str] = None


class AttendanceCreate(AttendanceBase):
    status: str = "PRESENT"
    role: str = "SWIMMER"


class PublicAttendanceCreate(AttendanceBase):
    member_id: uuid.UUID


class AttendanceResponse(AttendanceBase):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Optional fields populated by joins
    member_name: Optional[str] = None
    member_email: Optional[str] = None

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
