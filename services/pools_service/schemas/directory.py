"""Public-safe pool directory contracts. No partner rates, notes or contact records."""

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DirectoryPool(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    location_area: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    pool_length_m: Optional[float] = None
    depth_min_m: Optional[float] = None
    depth_max_m: Optional[float] = None
    indoor_outdoor: Optional[str] = None
    has_changing_rooms: Optional[bool] = None
    has_showers: Optional[bool] = None
    has_lockers: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_lifeguard: Optional[bool] = None
    last_verified_at: Optional[datetime] = None
    is_partner: bool
    bookable: bool
    rating_average: Optional[float] = None
    rating_count: int = 0


class DirectoryList(BaseModel):
    items: list[DirectoryPool]
    total: int
    page: int
    page_size: int


class DirectoryReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    review: str = Field(min_length=15, max_length=2000)


class DirectoryReview(BaseModel):
    id: uuid.UUID
    rating: int
    review: str
    created_at: datetime
    # Display only anonymous attribution, never account IDs/emails.
    attribution: str = "Swimmer"


class DirectoryReviews(BaseModel):
    items: list[DirectoryReview]
    total: int


class DirectorySuggestion(BaseModel):
    pool_name: str = Field(min_length=2, max_length=255)
    location_area: str = Field(min_length=2, max_length=255)
    address: Optional[str] = Field(default=None, max_length=500)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    details: Optional[str] = Field(default=None, max_length=1000)
    # Anonymous form honeypot (reject nonempty quietly).
    website: str = Field(default="", max_length=255)
