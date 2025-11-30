import uuid
from datetime import datetime
from typing import Optional

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
