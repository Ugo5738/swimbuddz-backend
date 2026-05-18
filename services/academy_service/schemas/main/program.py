"""Program schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from libs.common.currency import kobo_to_naira, naira_to_kobo
from services.academy_service.models import BillingType, ProgramLevel


class ProgramFAQItem(BaseModel):
    question: str
    answer: str


class ProgramBase(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: ProgramLevel
    duration_weeks: int
    default_capacity: int = 10
    # Pricing
    currency: str = "NGN"
    price_amount: int = 0  # API contract: naira (major unit)
    billing_type: BillingType = BillingType.ONE_TIME
    # Content
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    faq_json: Optional[List[ProgramFAQItem]] = None
    # Status
    is_published: bool = False


class ProgramCreate(ProgramBase):
    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_kobo(cls, value: int) -> int:
        return naira_to_kobo(value) or 0


class ProgramUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: Optional[ProgramLevel] = None
    duration_weeks: Optional[int] = None
    default_capacity: Optional[int] = None
    currency: Optional[str] = None
    price_amount: Optional[int] = None
    billing_type: Optional[BillingType] = None
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    faq_json: Optional[List[ProgramFAQItem]] = None
    is_published: Optional[bool] = None

    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_kobo(cls, value: Optional[int]) -> Optional[int]:
        return naira_to_kobo(value) if value is not None else None


class ProgramResponse(ProgramBase):
    id: UUID
    version: int = 1
    cover_image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime

    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_naira(cls, value: int) -> int:
        return int(kobo_to_naira(value)) if value is not None else 0

    model_config = ConfigDict(from_attributes=True)
