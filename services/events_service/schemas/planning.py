"""Admin schemas for recurring event templates and workbook imports."""

import uuid
from datetime import date, datetime, time
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.events_service.schemas.main import (
    EventAudience,
    EventBase,
    EventLocationType,
    EventTierAccess,
    EventVisibility,
)

EventFrequency = Literal["weekly", "monthly", "quarterly", "annual"]


class EventTemplateBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    event_type: str = Field(min_length=1, max_length=80)
    audience: EventAudience = "community"
    visibility: EventVisibility = "public"
    location_type: EventLocationType = "physical"
    timezone: str = "Africa/Lagos"
    location_area: Optional[str] = None
    is_location_private: bool = False
    location: Optional[str] = None
    local_start_time: time
    duration_minutes: int = Field(60, ge=15, le=1440)
    max_capacity: Optional[int] = Field(None, ge=1)
    tier_access: EventTierAccess = "community"
    pool_id: Optional[uuid.UUID] = None
    cost_naira: Optional[float] = Field(None, ge=0)
    frequency: EventFrequency
    interval: int = Field(1, ge=1, le=12)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    week_of_month: Optional[int] = Field(None, ge=-1, le=5)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    month_of_year: Optional[int] = Field(None, ge=1, le=12)
    starts_on: date
    ends_on: Optional[date] = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_rule(self) -> "EventTemplateBase":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone") from exc
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        if self.week_of_month == 0:
            raise ValueError("week_of_month must be 1-5 or -1 for last")
        if self.week_of_month is not None and self.day_of_week is None:
            raise ValueError("day_of_week is required with week_of_month")
        if self.frequency == "weekly" and self.day_of_week is None:
            raise ValueError("day_of_week is required for weekly templates")
        if self.frequency == "annual" and self.month_of_year is None:
            self.month_of_year = self.starts_on.month
        return self


class EventTemplateCreate(EventTemplateBase):
    pass


class EventTemplateUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    event_type: Optional[str] = Field(None, min_length=1, max_length=80)
    audience: Optional[EventAudience] = None
    visibility: Optional[EventVisibility] = None
    location_type: Optional[EventLocationType] = None
    timezone: Optional[str] = None
    location_area: Optional[str] = None
    is_location_private: Optional[bool] = None
    location: Optional[str] = None
    local_start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(None, ge=15, le=1440)
    max_capacity: Optional[int] = Field(None, ge=1)
    tier_access: Optional[EventTierAccess] = None
    pool_id: Optional[uuid.UUID] = None
    cost_naira: Optional[float] = Field(None, ge=0)
    frequency: Optional[EventFrequency] = None
    interval: Optional[int] = Field(None, ge=1, le=12)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    week_of_month: Optional[int] = Field(None, ge=-1, le=5)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    month_of_year: Optional[int] = Field(None, ge=1, le=12)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    is_active: Optional[bool] = None


class EventTemplateResponse(EventTemplateBase):
    id: uuid.UUID
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventOccurrence(BaseModel):
    local_date: date
    start_time: datetime
    end_time: datetime
    external_key: str


class EventOccurrenceRange(BaseModel):
    from_date: date
    to_date: date

    @model_validator(mode="after")
    def validate_range(self) -> "EventOccurrenceRange":
        if self.to_date < self.from_date:
            raise ValueError("to_date must be on or after from_date")
        if (self.to_date - self.from_date).days > 730:
            raise ValueError("Occurrence ranges cannot exceed two years")
        return self


class EventGenerationResponse(BaseModel):
    created: int
    skipped_existing: int
    occurrences: list[EventOccurrence]


class CalendarImportEvent(EventBase):
    external_key: str = Field(min_length=1, max_length=255)
    source_sheet: str = Field(min_length=1, max_length=100)
    source_row: int = Field(ge=1)


class CalendarImportPreviewItem(BaseModel):
    source_row: int
    selected: bool
    event: Optional[CalendarImportEvent] = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CalendarImportPreviewResponse(BaseModel):
    sheet_name: str
    valid_count: int
    invalid_count: int
    rows: list[CalendarImportPreviewItem]


class CalendarImportCommitRequest(BaseModel):
    rows: list[CalendarImportEvent] = Field(min_length=1, max_length=500)


class CalendarImportCommitResponse(BaseModel):
    created: int
    skipped_existing: int
    created_event_ids: list[uuid.UUID]
