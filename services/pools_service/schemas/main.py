"""Pydantic schemas for pools service."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.pools_service.models.enums import (
    IndoorOutdoor,
    PartnershipStatus,
    PoolType,
)

# ============================================================================
# POOL SCHEMAS
# ============================================================================


class PoolBase(BaseModel):
    # Identity
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    location_area: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Contact
    contact_person: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)

    # Physical
    pool_length_m: Optional[float] = Field(None, ge=0)
    depth_min_m: Optional[float] = Field(None, ge=0)
    depth_max_m: Optional[float] = Field(None, ge=0)
    number_of_lanes: Optional[int] = Field(None, ge=0)
    indoor_outdoor: Optional[IndoorOutdoor] = None
    max_swimmers_capacity: Optional[int] = Field(None, ge=0)

    # Scores (1-5)
    water_quality: Optional[int] = Field(None, ge=1, le=5)
    good_for_beginners: Optional[int] = Field(None, ge=1, le=5)
    good_for_training: Optional[int] = Field(None, ge=1, le=5)
    ease_of_access: Optional[int] = Field(None, ge=1, le=5)
    management_cooperation: Optional[int] = Field(None, ge=1, le=5)
    partnership_potential: Optional[int] = Field(None, ge=1, le=5)
    overall_score: Optional[int] = Field(None, ge=1, le=5)

    # Availability
    available_days_times: Optional[dict] = None
    exclusive_lanes_available: Optional[bool] = None

    # Pricing
    price_per_swimmer_ngn: Optional[Decimal] = Field(None, ge=0)
    flat_session_fee_ngn: Optional[Decimal] = Field(None, ge=0)
    group_discount_available: Optional[bool] = None

    # Facilities
    has_changing_rooms: Optional[bool] = None
    has_showers: Optional[bool] = None
    has_lockers: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_lifeguard: Optional[bool] = None

    # Operations
    video_content_allowed: Optional[bool] = None
    trial_session_possible: Optional[bool] = None

    # Partnership
    partnership_status: PartnershipStatus = PartnershipStatus.PROSPECT

    # Meta
    pool_type: Optional[PoolType] = None
    notes: Optional[str] = None
    is_active: bool = True


class PoolCreate(PoolBase):
    pass


class PoolUpdate(BaseModel):
    # All fields optional for partial update
    name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)
    location_area: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    contact_person: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)

    pool_length_m: Optional[float] = Field(None, ge=0)
    depth_min_m: Optional[float] = Field(None, ge=0)
    depth_max_m: Optional[float] = Field(None, ge=0)
    number_of_lanes: Optional[int] = Field(None, ge=0)
    indoor_outdoor: Optional[IndoorOutdoor] = None
    max_swimmers_capacity: Optional[int] = Field(None, ge=0)

    water_quality: Optional[int] = Field(None, ge=1, le=5)
    good_for_beginners: Optional[int] = Field(None, ge=1, le=5)
    good_for_training: Optional[int] = Field(None, ge=1, le=5)
    ease_of_access: Optional[int] = Field(None, ge=1, le=5)
    management_cooperation: Optional[int] = Field(None, ge=1, le=5)
    partnership_potential: Optional[int] = Field(None, ge=1, le=5)
    overall_score: Optional[int] = Field(None, ge=1, le=5)

    available_days_times: Optional[dict] = None
    exclusive_lanes_available: Optional[bool] = None

    price_per_swimmer_ngn: Optional[Decimal] = Field(None, ge=0)
    flat_session_fee_ngn: Optional[Decimal] = Field(None, ge=0)
    group_discount_available: Optional[bool] = None

    has_changing_rooms: Optional[bool] = None
    has_showers: Optional[bool] = None
    has_lockers: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_lifeguard: Optional[bool] = None

    video_content_allowed: Optional[bool] = None
    trial_session_possible: Optional[bool] = None

    partnership_status: Optional[PartnershipStatus] = None
    pool_type: Optional[PoolType] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class PoolResponse(PoolBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PoolListResponse(BaseModel):
    """Paginated pool list."""

    items: list[PoolResponse]
    total: int
    page: int
    page_size: int
