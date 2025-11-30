from datetime import datetime, time
from typing import Optional
import uuid

from pydantic import BaseModel, Field


class SessionTemplateBase(BaseModel):
    title: str
    description: Optional[str] = None
    location: str
    pool_fee: int = 0
    ride_share_fee: int = 0
    capacity: int = 20
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday, 6=Sunday")
    start_time: time
    duration_minutes: int
    auto_generate: bool = False


class SessionTemplateCreate(SessionTemplateBase):
    pass


class SessionTemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    pool_fee: Optional[int] = None
    ride_share_fee: Optional[int] = None
    capacity: Optional[int] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = None
    auto_generate: Optional[bool] = None
    is_active: Optional[bool] = None


class SessionTemplateResponse(SessionTemplateBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class GenerateSessionsRequest(BaseModel):
    weeks: int = Field(..., gt=0, le=52, description="Number of weeks to generate")
    skip_conflicts: bool = Field(
        True, description="Skip dates that already have sessions"
    )
