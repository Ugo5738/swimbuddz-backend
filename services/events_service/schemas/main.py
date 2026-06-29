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
    # Optional entry fee — API accepts/returns naira (float). DB stores kobo (int).
    cost_naira: Optional[float] = None  # null = free


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
    cost_naira: Optional[float] = None  # null = free


class EventResponse(BaseModel):
    """Event response schema — cost_naira converted from cost_kobo on read."""

    id: uuid.UUID
    title: str
    description: Optional[str] = None
    event_type: str
    location: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: str
    cost_naira: Optional[float] = None  # null = free
    # Member-created pool meets (event_type="open_swim"):
    pool_id: Optional[uuid.UUID] = None  # null = no pool / free meet
    pool_fee_naira: Optional[float] = None  # snapshotted per-swimmer pool fee
    organizer_surcharge_naira: Optional[float] = None  # organizer add-on per attendee
    # Effective per-attendee charge: cost_naira (admin events) OR
    # pool_fee + surcharge (open_swim). null/0 = free.
    total_cost_naira: Optional[float] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    rsvp_count: Optional[dict] = None  # {"going": 5, "maybe": 2, "not_going": 1}

    model_config = ConfigDict(from_attributes=True)


class OpenSwimCreate(BaseModel):
    """Schema for a member creating their own open-swim meet.

    `event_type` is forced to "open_swim" server-side. If `pool_id` is set the
    meet is a paid pool meet (the per-swimmer fee is snapshotted from the pool and
    the optional surcharge added); if null it is a free/informal meet.
    """

    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: str = "community"
    pool_id: Optional[uuid.UUID] = None  # null = free / informal venue
    # Organizer's optional add-on per attendee (naira). Settled manually off-platform.
    organizer_surcharge_naira: Optional[float] = None


class OpenSwimUpdate(BaseModel):
    """Schema for a member editing their own open-swim meet (all fields optional)."""

    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    organizer_surcharge_naira: Optional[float] = None


class RSVPCreate(BaseModel):
    """Schema for creating/updating an RSVP."""

    status: str  # going/maybe/not_going
    pay_with_bubbles: bool = False  # If True, debit wallet for the event fee on "going"
    # Required (true) when RSVPing "going" to a paid peer-organized meet.
    waiver_accepted: bool = False


class RSVPResponse(BaseModel):
    """RSVP response schema."""

    id: uuid.UUID
    event_id: uuid.UUID
    member_id: uuid.UUID
    status: str
    wallet_transaction_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
