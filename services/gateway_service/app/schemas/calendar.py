"""Response shapes for the cross-service calendar read model."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

CalendarAudience = Literal["community", "club", "academy"]
CalendarSource = str
CalendarVisibility = Literal["public", "members_only", "invite_only"]
CalendarLocationType = Literal["physical", "online", "hybrid"]


class CalendarItemResponse(BaseModel):
    """One visible item from a domain-owned session or event."""

    id: str
    source: CalendarSource = Field(min_length=1)
    primary_audience: CalendarAudience
    audiences: list[CalendarAudience] = Field(min_length=1)
    # Deprecated compatibility alias for primary_audience.
    audience: CalendarAudience
    kind: str
    visibility: CalendarVisibility = "public"
    access_level: str = "public"
    location_type: CalendarLocationType = "physical"
    title: str
    description: Optional[str] = None
    starts_at: datetime
    ends_at: Optional[datetime] = None
    timezone: str = "Africa/Lagos"
    location_name: Optional[str] = None
    location_area: Optional[str] = None
    pool_id: Optional[str] = None
    status: str = "scheduled"
    href: str
    bookable: bool = False
    viewer_can_attend: bool = False


class CalendarActivityType(BaseModel):
    """One activity type represented in the current calendar result."""

    key: str
    label: str


class CalendarResponse(BaseModel):
    """Calendar items plus range and partial-service status."""

    items: list[CalendarItemResponse] = Field(default_factory=list)
    range_start: datetime
    range_end: datetime
    available_audiences: list[CalendarAudience] = Field(default_factory=list)
    available_activity_types: list[CalendarActivityType] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
