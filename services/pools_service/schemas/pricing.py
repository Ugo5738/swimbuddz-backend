"""Schemas for operating areas, rates, and cost quotes."""

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    model_validator,
)

ActivityScope = Annotated[
    Literal["all", "community", "club", "academy"],
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["all", "community", "club", "academy"],
        }
    ),
]
AreaType = Literal["country", "market", "commercial_band", "locality"]
ChargeBasis = Literal[
    "per_attendee",
    "per_staff",
    "per_hour",
    "per_lane",
    "flat_session",
]


class OperatingAreaBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    area_type: AreaType = "locality"
    parent_id: Optional[uuid.UUID] = None
    country_code: str = Field(default="NG", min_length=2, max_length=2)
    timezone: str = "Africa/Lagos"
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    is_active: bool = True


class OperatingAreaCreate(OperatingAreaBase):
    pass


class OperatingAreaUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255)
    area_type: Optional[AreaType] = None
    parent_id: Optional[uuid.UUID] = None
    country_code: Optional[str] = Field(None, min_length=2, max_length=2)
    timezone: Optional[str] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    is_active: Optional[bool] = None


class OperatingAreaResponse(OperatingAreaBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EffectiveRateBase(BaseModel):
    activity_scope: ActivityScope = "all"
    charge_basis: ChargeBasis
    amount_naira: float = Field(ge=0)
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    effective_from: date
    effective_to: Optional[date] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    starts_after: Optional[time] = None
    ends_before: Optional[time] = None
    minimum_quantity: int = Field(default=1, ge=1)
    notes: Optional[str] = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_effective_range(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from")
        return self


class PoolRateCreate(EffectiveRateBase):
    pool_id: uuid.UUID
    description: Optional[str] = Field(None, max_length=255)


class PoolRateUpdate(BaseModel):
    activity_scope: Optional[ActivityScope] = None
    charge_basis: Optional[ChargeBasis] = None
    amount_naira: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    starts_after: Optional[time] = None
    ends_before: Optional[time] = None
    minimum_quantity: Optional[int] = Field(None, ge=1)
    description: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class PoolRateResponse(PoolRateCreate):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class OperatingCostRateCreate(EffectiveRateBase):
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=255)
    operating_area_id: Optional[uuid.UUID] = None
    pool_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = Field(None, max_length=255)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.operating_area_id and self.pool_id:
            raise ValueError("Choose either an operating area or a pool, not both")
        return self


class OperatingCostRateUpdate(BaseModel):
    category: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, min_length=1, max_length=255)
    operating_area_id: Optional[uuid.UUID] = None
    pool_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = Field(None, max_length=255)
    activity_scope: Optional[ActivityScope] = None
    charge_basis: Optional[ChargeBasis] = None
    amount_naira: Optional[float] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    starts_after: Optional[time] = None
    ends_before: Optional[time] = None
    minimum_quantity: Optional[int] = Field(None, ge=1)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class OperatingCostRateResponse(OperatingCostRateCreate):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CostQuoteRequest(BaseModel):
    pool_id: uuid.UUID
    activity_scope: Literal["community", "club", "academy"]
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Africa/Lagos"
    expected_attendees: int = Field(ge=1)
    expected_staff: int = Field(default=0, ge=0)
    lanes: int = Field(default=1, ge=1)


class CostQuoteLine(BaseModel):
    category: str
    description: str
    charge_basis: ChargeBasis
    unit_cost_naira: float
    quantity: float
    total_cost_naira: float
    source_rate_type: Literal["pool_rate", "operating_cost_rate"]
    source_rate_id: uuid.UUID


class CostQuoteResponse(BaseModel):
    pool_id: uuid.UUID
    operating_area_id: Optional[uuid.UUID] = None
    activity_scope: str
    currency: str
    expected_attendees: int
    lines: list[CostQuoteLine]
    estimated_total_cost_naira: float
    estimated_cost_per_attendee_naira: float
    warnings: list[str] = Field(default_factory=list)
