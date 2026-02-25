import uuid
from datetime import datetime, time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from services.sessions_service.models import SessionType


class SessionTemplateBase(BaseModel):
    title: str
    description: Optional[str] = None
    location: str
    session_type: SessionType = SessionType.COMMUNITY
    # API uses naira (float); DB stores kobo (int). Routers handle conversion.
    pool_fee: float = 0.0
    ride_share_fee: float = 0.0
    capacity: int = 20
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday, 6=Sunday")
    start_time: time
    duration_minutes: int
    auto_generate: bool = False
    ride_share_config: Optional[List[Dict]] = None


class SessionTemplateCreate(SessionTemplateBase):
    pass


class SessionTemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    pool_fee: Optional[float] = None  # naira — router converts to kobo on write
    ride_share_fee: Optional[float] = None  # naira — router converts to kobo on write
    capacity: Optional[int] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = None
    auto_generate: Optional[bool] = None
    is_active: Optional[bool] = None
    ride_share_config: Optional[List[Dict]] = None


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
            "location": obj.location,
            "session_type": obj.session_type,
            "pool_fee": (obj.pool_fee or 0) / 100.0,
            "ride_share_fee": (obj.ride_share_fee or 0) / 100.0,
            "capacity": obj.capacity,
            "day_of_week": obj.day_of_week,
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
    weeks: int = Field(..., gt=0, le=52, description="Number of weeks to generate")
    skip_conflicts: bool = Field(
        True, description="Skip dates that already have sessions"
    )
