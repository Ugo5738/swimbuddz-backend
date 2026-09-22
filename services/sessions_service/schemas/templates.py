import uuid
from datetime import date, datetime, time
from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field, model_validator

from services.sessions_service.models import SessionType
from services.sessions_service.schemas.main import SessionCostLine

SessionTemplateFrequency = Literal["weekly", "monthly", "quarterly", "annual"]


class ClubTemplatePricing(BaseModel):
    pricing_expected_attendees: int = Field(ge=1, le=500)
    margin_type: Literal["fixed_per_attendee", "percentage"] = "fixed_per_attendee"
    margin_value: float = Field(ge=0)
    cost_lines: list[SessionCostLine] = Field(default_factory=list)
    expected_staff: int = Field(default=0, ge=0, le=50)
    lanes: int = Field(default=1, ge=1, le=50)


class SessionTemplateBase(BaseModel):
    club_id: Optional[uuid.UUID] = None
    club_access_mode: Literal["plan_included", "active_club", "paid_addon"] = (
        "plan_included"
    )
    pricing_settings: Optional[ClubTemplatePricing] = None
    title: str
    description: Optional[str] = None
    # Pool reference — at least one of pool_id / location must be supplied.
    # New templates send pool_id (the pools-registry UUID); legacy templates
    # may only have the `location` string.
    pool_id: Optional[uuid.UUID] = None
    location: Optional[str] = None
    location_name: Optional[str] = None
    session_type: SessionType = SessionType.COMMUNITY
    # Every new Club template belongs to one Club. ``pod_id`` optionally
    # narrows generated sessions to a Pod inside that Club.
    pod_id: Optional[uuid.UUID] = None
    # API uses naira (float); DB stores kobo (int). Routers handle conversion.
    pool_fee: float = 0.0
    ride_share_fee: float = 0.0
    capacity: int = Field(default=20, ge=1, le=500)
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday, 6=Sunday")
    frequency: SessionTemplateFrequency = "weekly"
    interval: int = Field(1, ge=1, le=12)
    week_of_month: Optional[int] = Field(None, ge=-1, le=5)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    month_of_year: Optional[int] = Field(None, ge=1, le=12)
    starts_on: date = Field(default_factory=date.today)
    ends_on: Optional[date] = None
    start_time: time
    duration_minutes: int = Field(ge=15, le=480)
    auto_generate: bool = False
    ride_share_config: Optional[List[Dict]] = None


class SessionTemplateCreate(SessionTemplateBase):
    @model_validator(mode="after")
    def _require_pool_reference(self) -> "SessionTemplateCreate":
        if self.session_type == SessionType.EVENT:
            raise ValueError(
                "Create Event sessions from a concrete Event occurrence so event_id is preserved"
            )
        if self.session_type != SessionType.CLUB and (
            self.club_id or self.club_access_mode != "plan_included"
        ):
            raise ValueError(
                "Only Club templates can specify a Club or Club access mode"
            )
        if not self.pool_id and not self.location:
            raise ValueError("Either pool_id or location must be provided")
        if self.session_type == SessionType.CLUB and self.club_id is None:
            raise ValueError("club_id is required for Club session templates")
        if self.session_type != SessionType.CLUB and (
            self.club_id is not None or self.pod_id is not None
        ):
            raise ValueError("Only club session templates may set club_id or pod_id")
        if self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        if self.week_of_month == 0:
            raise ValueError("week_of_month must be 1-5 or -1 for last")
        if self.week_of_month is not None and self.frequency == "weekly":
            raise ValueError("week_of_month is only valid for monthly-style rules")
        if self.frequency == "annual" and self.month_of_year is None:
            self.month_of_year = self.starts_on.month
        return self


class SessionTemplateUpdate(BaseModel):
    club_id: Optional[uuid.UUID] = None
    club_access_mode: Optional[
        Literal["plan_included", "active_club", "paid_addon"]
    ] = None
    pricing_settings: Optional[ClubTemplatePricing] = None
    title: Optional[str] = None
    description: Optional[str] = None
    pool_id: Optional[uuid.UUID] = None
    location: Optional[str] = None
    location_name: Optional[str] = None
    session_type: Optional[SessionType] = None
    pod_id: Optional[uuid.UUID] = None
    pool_fee: Optional[float] = None  # naira — router converts to kobo on write
    ride_share_fee: Optional[float] = None  # naira — router converts to kobo on write
    capacity: Optional[int] = Field(None, ge=1, le=500)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    frequency: Optional[SessionTemplateFrequency] = None
    interval: Optional[int] = Field(None, ge=1, le=12)
    week_of_month: Optional[int] = Field(None, ge=-1, le=5)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    month_of_year: Optional[int] = Field(None, ge=1, le=12)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(None, ge=15, le=480)
    auto_generate: Optional[bool] = None
    is_active: Optional[bool] = None
    ride_share_config: Optional[List[Dict]] = None

    @model_validator(mode="after")
    def _validate_pod_scope(self) -> "SessionTemplateUpdate":
        if self.session_type == SessionType.EVENT:
            raise ValueError(
                "Create Event sessions from a concrete Event occurrence so event_id is preserved"
            )
        if self.session_type and self.session_type != SessionType.CLUB:
            if self.club_id is not None or self.pod_id is not None:
                raise ValueError(
                    "Only club session templates may set club_id or pod_id"
                )
        return self


class SessionTemplateResponse(SessionTemplateBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

    @classmethod
    def model_validate(cls, obj, *, strict=None, from_attributes=None, context=None):  # type: ignore[override]
        """Override to convert kobo→naira when reading from the ORM."""
        if isinstance(obj, dict):
            return super().model_validate(
                obj, strict=strict, from_attributes=from_attributes, context=context
            )
        # ORM instance — convert fee kobo → naira
        data = {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "pool_id": obj.pool_id,
            "location": obj.location,
            "location_name": obj.location_name,
            "session_type": obj.session_type,
            "club_id": getattr(obj, "club_id", None),
            "pod_id": obj.pod_id,
            "club_access_mode": getattr(obj, "club_access_mode", "plan_included"),
            "pricing_settings": getattr(obj, "pricing_settings", None) or None,
            "pool_fee": (obj.pool_fee or 0) / 100.0,
            "ride_share_fee": (obj.ride_share_fee or 0) / 100.0,
            "capacity": obj.capacity,
            "day_of_week": obj.day_of_week,
            "frequency": getattr(obj, "frequency", "weekly"),
            "interval": getattr(obj, "interval", 1),
            "week_of_month": getattr(obj, "week_of_month", None),
            "day_of_month": getattr(obj, "day_of_month", None),
            "month_of_year": getattr(obj, "month_of_year", None),
            "starts_on": getattr(obj, "starts_on", date.today()),
            "ends_on": getattr(obj, "ends_on", None),
            "start_time": obj.start_time,
            "duration_minutes": obj.duration_minutes,
            "auto_generate": obj.auto_generate,
            "ride_share_config": obj.ride_share_config,
            "is_active": obj.is_active,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }
        return super().model_validate(data)


class GenerateSessionsRequest(BaseModel):
    weeks: Optional[int] = Field(
        None,
        gt=0,
        le=52,
        description="Legacy rolling window, measured in weeks.",
    )
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    dates: list[date] = Field(default_factory=list, max_length=52)
    skip_conflicts: bool = Field(
        True, description="Skip dates that already have sessions"
    )

    @model_validator(mode="after")
    def validate_generation_window(self) -> "GenerateSessionsRequest":
        has_range = self.from_date is not None or self.to_date is not None
        modes = int(self.weeks is not None) + int(has_range) + int(bool(self.dates))
        if modes != 1:
            raise ValueError("Choose weeks, a date range, or specific dates")
        if has_range:
            if self.from_date is None or self.to_date is None:
                raise ValueError("from_date and to_date are required together")
            if self.to_date < self.from_date:
                raise ValueError("to_date must be on or after from_date")
            if (self.to_date - self.from_date).days > 730:
                raise ValueError("Date ranges cannot exceed two years")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("Specific dates must be unique")
        return self
