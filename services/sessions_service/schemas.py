import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from services.sessions_service.models import SessionLocation, SessionType, SessionStatus


class SessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None

    session_type: SessionType = SessionType.CLUB
    status: SessionStatus = SessionStatus.SCHEDULED

    # Location (enum or custom string)
    location: Optional[SessionLocation] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    # Timing
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Africa/Lagos"

    # Capacity & Fees
    capacity: int = 20
    pool_fee: float = 0.0
    ride_share_fee: float = 0.0

    # Context links
    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    booking_id: Optional[uuid.UUID] = None

    # Cohort-specific
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class SessionCreate(SessionBase):
    pass


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None

    session_type: Optional[SessionType] = None
    status: Optional[SessionStatus] = None

    location: Optional[SessionLocation] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    timezone: Optional[str] = None

    capacity: Optional[int] = None
    pool_fee: Optional[float] = None
    ride_share_fee: Optional[float] = None

    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    booking_id: Optional[uuid.UUID] = None

    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class SessionResponse(SessionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    template_id: Optional[uuid.UUID] = None
    is_recurring_instance: bool = False

    model_config = ConfigDict(from_attributes=True)
