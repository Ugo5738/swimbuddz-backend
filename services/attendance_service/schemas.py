import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.attendance_service.models import PaymentStatus


class AttendanceBase(BaseModel):
    needs_ride: bool = False
    can_offer_ride: bool = False
    ride_notes: Optional[str] = None


class AttendanceCreate(AttendanceBase):
    pass


class AttendanceResponse(AttendanceBase):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    payment_status: PaymentStatus
    total_fee: float
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
