"""Pydantic schemas for Events Service."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EventBase(BaseModel):
    """Base event schema."""
    title: str
    description: Optional[str] = None
    event_type: str  # social/volunteer/beach_day/watch_party/cleanup/training
    location: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: str = "community"  # community/club/academy


class EventCreate(EventBase):
    """Schema for creating an event."""
    pass


class EventUpdate(BaseModel):
    """Schema for updating an event."""
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: Optional[str] = None


class EventResponse(EventBase):
    """Event response schema."""
    id: uuid.UUID
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    rsvp_count: Optional[dict] = None  # {"going": 5, "maybe": 2, "not_going": 1}

    model_config = ConfigDict(from_attributes=True)


class RSVPCreate(BaseModel):
    """Schema for creating/updating an RSVP."""
    status: str  # going/maybe/not_going


class RSVPResponse(BaseModel):
    """RSVP response schema."""
    id: uuid.UUID
    event_id: uuid.UUID
    member_id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
