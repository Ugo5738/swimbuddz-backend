"""Schemas for the Club entity."""

import re
import uuid
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.members_service.models.enums import DayOfWeek

# slug: lowercase, digits, single hyphens; 2-40 chars; no leading/trailing hyphen
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ClubBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str = Field(..., min_length=2, max_length=40)
    description: Optional[str] = None
    location: Optional[str] = None
    # Default session schedule pods inherit at creation. See
    # docs/club/POD_OPERATIONS.md "Saturday session — anchored, with override".
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters/numbers separated by hyphens"
            )
        return v


class ClubCreate(ClubBase):
    is_active: bool = True


class ClubUpdate(BaseModel):
    """All fields optional. slug accepts the same format if provided."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    slug: Optional[str] = Field(default=None, min_length=2, max_length=40)
    description: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters/numbers separated by hyphens"
            )
        return v


class ClubResponse(ClubBase):
    id: uuid.UUID
    is_active: bool
    # Schedule fields are NOT optional in the response — every Club row in
    # the DB has them (server defaults), so the API always returns concrete
    # values. We override the base's Optional types here.
    default_session_day: DayOfWeek
    default_session_time: time
    default_session_duration_minutes: int
    default_pool_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
