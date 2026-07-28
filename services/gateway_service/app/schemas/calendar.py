"""Response shapes for the cross-service calendar read model."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

CalendarAudience = Literal["community", "club", "academy"]
CalendarSource = Literal["session", "event"]


class CalendarItemResponse(BaseModel):
    """One visible item from a domain-owned session or event."""

    id: str
    source: CalendarSource
    audience: CalendarAudience
    kind: str
    title: str
    description: Optional[str] = None
    starts_at: datetime
    ends_at: Optional[datetime] = None
    timezone: str = "Africa/Lagos"
    location_name: Optional[str] = None
    pool_id: Optional[str] = None
    status: str = "scheduled"
    href: str
    bookable: bool = False


class CalendarResponse(BaseModel):
    """Calendar items plus range and partial-service status."""

    items: list[CalendarItemResponse] = Field(default_factory=list)
    range_start: datetime
    range_end: datetime
    available_audiences: list[CalendarAudience] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
