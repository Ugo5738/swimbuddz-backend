import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.attendance_service.models import PaymentStatus, RideShareOption


class AttendanceBase(BaseModel):
    ride_share_option: RideShareOption = RideShareOption.NONE
    needs_ride: bool = False
    can_offer_ride: bool = False
    ride_notes: Optional[str] = None
    pickup_location: Optional[str] = None


class AttendanceCreate(AttendanceBase):
    pass


class PublicAttendanceCreate(AttendanceBase):
    member_id: uuid.UUID
    payment_status: PaymentStatus = PaymentStatus.PENDING


class AttendanceResponse(AttendanceBase):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    payment_status: PaymentStatus
    total_fee: float
    created_at: datetime
    updated_at: datetime
    
    # Optional fields populated by joins
    member_name: Optional[str] = None
    member_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
