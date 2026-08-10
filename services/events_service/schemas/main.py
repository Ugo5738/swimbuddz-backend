"""Pydantic schemas for Events Service."""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EventAudience = Literal["community", "club", "academy"]
EventVisibility = Literal["public", "members_only", "invite_only"]
EventStatus = Literal["draft", "published", "cancelled"]
EventLocationType = Literal["physical", "online", "hybrid"]
EventTierAccess = Literal["public", "community", "club", "academy", "invite_only"]
EventPricingMode = Literal["free", "included", "fixed", "cost_plus"]
MarginType = Literal["fixed_per_attendee", "percentage"]
ReminderHour = Annotated[int, Field(ge=1, le=720)]


class EventCostLine(BaseModel):
    category: str
    description: str
    charge_basis: str
    unit_cost_naira: float = Field(ge=0)
    quantity: float = Field(ge=0)
    source_rate_type: Optional[str] = None
    source_rate_id: Optional[uuid.UUID] = None


class EventBase(BaseModel):
    """Base event schema."""

    title: str
    description: Optional[str] = None
    event_type: str  # social/volunteer/beach_day/watch_party/cleanup/training
    audience: EventAudience = "community"
    visibility: EventVisibility = "public"
    status: EventStatus = "published"
    location_type: EventLocationType = "physical"
    timezone: str = "Africa/Lagos"
    location_area: Optional[str] = None
    is_location_private: bool = False
    location: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: EventTierAccess = "community"
    pool_id: Optional[uuid.UUID] = None
    # Optional entry fee — API accepts/returns naira (float). DB stores kobo (int).
    cost_naira: Optional[float] = None  # null = free
    pricing_mode: EventPricingMode = "fixed"
    pricing_expected_attendees: Optional[int] = Field(None, ge=1)
    cost_lines: list[EventCostLine] = Field(default_factory=list)
    margin_type: MarginType = "fixed_per_attendee"
    margin_value: float = Field(default=0, ge=0)
    email_reminder_hours: list[ReminderHour] = Field(default_factory=list, max_length=8)


class EventCreate(EventBase):
    """Schema for creating an event."""

    pass


class EventUpdate(BaseModel):
    """Schema for updating an event."""

    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    audience: Optional[EventAudience] = None
    visibility: Optional[EventVisibility] = None
    status: Optional[EventStatus] = None
    location_type: Optional[EventLocationType] = None
    timezone: Optional[str] = None
    location_area: Optional[str] = None
    is_location_private: Optional[bool] = None
    location: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: Optional[EventTierAccess] = None
    pool_id: Optional[uuid.UUID] = None
    cost_naira: Optional[float] = None  # null = free
    pricing_mode: Optional[EventPricingMode] = None
    pricing_expected_attendees: Optional[int] = Field(None, ge=1)
    cost_lines: Optional[list[EventCostLine]] = None
    margin_type: Optional[MarginType] = None
    margin_value: Optional[float] = Field(None, ge=0)
    email_reminder_hours: Optional[list[ReminderHour]] = Field(None, max_length=8)


class EventResponse(BaseModel):
    """Event response schema — cost_naira converted from cost_kobo on read."""

    id: uuid.UUID
    title: str
    description: Optional[str] = None
    event_type: str
    audience: EventAudience
    visibility: EventVisibility
    status: EventStatus
    location_type: EventLocationType
    timezone: str
    location_area: Optional[str] = None
    is_location_private: bool
    location: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    max_capacity: Optional[int] = None
    tier_access: EventTierAccess
    cost_naira: Optional[float] = None  # null = free
    pricing_mode: EventPricingMode = "fixed"
    pricing_expected_attendees: Optional[int] = None
    cost_lines: list[EventCostLine] = Field(default_factory=list)
    estimated_total_cost_naira: float = 0
    estimated_cost_per_attendee_naira: float = 0
    margin_type: MarginType = "fixed_per_attendee"
    margin_value: float = 0
    margin_amount_per_attendee_naira: float = 0
    email_reminder_hours: list[ReminderHour] = Field(default_factory=list)
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
    viewer_can_attend: bool = False
    viewer_invited: bool = False

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
    tier_access: EventTierAccess = "community"
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


class EventInviteCreate(BaseModel):
    """Bulk invitation input for a private event."""

    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class EventInviteResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    member_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
