"""API payloads for self-directed Pool Access, distinct from scheduled sessions."""

import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class OfferInput(BaseModel):
    pool_id: uuid.UUID
    title: str = Field(min_length=3, max_length=160)
    starts_at: datetime
    ends_at: datetime
    capacity: int = Field(ge=1, le=1000)
    selling_price_kobo: int = Field(gt=0)
    negotiated_cost_kobo: int = Field(ge=0)
    cost_basis: Literal["per_person", "per_group"] = "per_person"
    currency: str = Field(default="NGN", pattern=r"^[A-Z]{3}$")
    self_directed_permitted: bool = False
    admissions_require_lifeguard: bool = True
    amenities: list[str] = Field(default_factory=list)
    access_rules: str = ""
    cancellation_policy: str = ""
    public_booking_enabled: bool = False

    @model_validator(mode="after")
    def valid_window(self):
        if (
            not self.starts_at.tzinfo
            or not self.ends_at.tzinfo
            or self.starts_at >= self.ends_at
        ):
            raise ValueError("Use timezone-aware start/end dates in increasing order")
        return self


class OfferOut(BaseModel):
    """Public catalog representation: NEVER expose negotiated partner costs."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    pool_id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    capacity: int
    selling_price_kobo: int
    currency: str
    status: str
    amenities: list[str]
    access_rules: str
    cancellation_policy: str


class AdminOfferOut(OfferInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str


class GuestInput(BaseModel):
    name: str = Field(min_length=2, max_length=150)


class BookingInput(BaseModel):
    offer_id: uuid.UUID
    guests: list[GuestInput] = Field(min_length=1, max_length=25)
    idempotency_key: str = Field(min_length=8, max_length=100)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    offer_id: uuid.UUID
    headcount: int
    selling_total_kobo: int
    status: str
    payment_reference: str | None
    hold_expires_at: datetime


class ExternalPaymentEvidence(BaseModel):
    """An admin may request verification, never supply an arbitrary paid flag."""

    reference: str = Field(min_length=6, max_length=160)


class CheckInInput(BaseModel):
    ticket: str = Field(min_length=70, max_length=200)
