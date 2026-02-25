import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator
from services.sessions_service.models import SessionLocation, SessionStatus, SessionType


class SessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None

    session_type: SessionType = SessionType.CLUB
    status: Optional[SessionStatus] = None  # Defaults to DRAFT at creation

    # Location (enum or custom string)
    location: Optional[SessionLocation] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    # Timing
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Africa/Lagos"

    # Capacity & Fees — API layer uses Naira (float); DB stores kobo (int).
    capacity: int = 20
    pool_fee: float = 0.0  # naira input/output
    ride_share_fee: float = 0.0  # naira input/output

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
    pool_fee: Optional[float] = None  # naira — router converts to kobo on write
    ride_share_fee: Optional[float] = None  # naira — router converts to kobo on write

    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    booking_id: Optional[uuid.UUID] = None

    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class SessionResponse(SessionBase):
    id: uuid.UUID
    status: SessionStatus  # Override to make required in response
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    template_id: Optional[uuid.UUID] = None
    is_recurring_instance: bool = False

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _convert_kobo_to_naira(cls, obj):
        """When reading from the ORM, convert kobo fee fields to naira for the API."""
        if isinstance(obj, dict):
            return obj
        # ORM instance: read attributes and convert integer kobo → float naira
        pool_fee_kobo = getattr(obj, "pool_fee", 0) or 0
        ride_share_fee_kobo = getattr(obj, "ride_share_fee", 0) or 0
        return {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "notes": obj.notes,
            "session_type": obj.session_type,
            "status": obj.status,
            "location": obj.location,
            "location_name": obj.location_name,
            "location_address": obj.location_address,
            "starts_at": obj.starts_at,
            "ends_at": obj.ends_at,
            "timezone": obj.timezone,
            "capacity": obj.capacity,
            "pool_fee": pool_fee_kobo / 100.0,
            "ride_share_fee": ride_share_fee_kobo / 100.0,
            "cohort_id": obj.cohort_id,
            "event_id": obj.event_id,
            "booking_id": obj.booking_id,
            "week_number": obj.week_number,
            "lesson_title": obj.lesson_title,
            "template_id": obj.template_id,
            "is_recurring_instance": obj.is_recurring_instance,
            "published_at": obj.published_at,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }
