"""Pydantic schemas for pool submissions (member-contributed pool data)."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from services.pools_service.models.enums import PoolType
from services.pools_service.models.pool_submission import PoolSubmissionStatus


class PoolSubmissionCreate(BaseModel):
    """Payload a member sends when suggesting a pool."""

    pool_name: str = Field(..., min_length=2, max_length=255)
    location_area: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=2000)
    pool_type: Optional[PoolType] = None

    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[EmailStr] = None

    has_changing_rooms: Optional[bool] = None
    has_showers: Optional[bool] = None
    has_lockers: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_lifeguard: Optional[bool] = None

    visit_frequency: Optional[str] = Field(None, max_length=50)
    member_rating: Optional[int] = Field(None, ge=1, le=5)
    member_notes: Optional[str] = Field(None, max_length=4000)
    photo_url: Optional[str] = Field(None, max_length=2000)


class PoolSubmissionResponse(BaseModel):
    """Submission record returned to member (their own) and admins."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID

    submitter_auth_id: str
    submitter_display_name: Optional[str]
    submitter_email: Optional[str]

    pool_name: str
    location_area: Optional[str]
    address: Optional[str]
    pool_type: Optional[PoolType]
    contact_phone: Optional[str]
    contact_email: Optional[str]

    has_changing_rooms: Optional[bool]
    has_showers: Optional[bool]
    has_lockers: Optional[bool]
    has_parking: Optional[bool]
    has_lifeguard: Optional[bool]

    visit_frequency: Optional[str]
    member_rating: Optional[int]
    member_notes: Optional[str]
    photo_url: Optional[str]

    status: PoolSubmissionStatus
    reviewed_by_auth_id: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    promoted_pool_id: Optional[uuid.UUID]

    reward_granted: bool
    reward_bubbles: Optional[int]

    created_at: datetime
    updated_at: datetime


class PoolSubmissionListResponse(BaseModel):
    items: list[PoolSubmissionResponse]
    total: int
    page: int
    page_size: int


class PoolSubmissionApproveRequest(BaseModel):
    """Admin approves a submission — optionally grants Bubbles and adds review notes."""

    reward_bubbles: int = Field(
        default=500,
        ge=0,
        le=100000,
        description="Bubbles to grant submitter (0 to skip reward)",
    )
    review_notes: Optional[str] = Field(None, max_length=2000)


class PoolSubmissionRejectRequest(BaseModel):
    review_notes: str = Field(..., min_length=1, max_length=2000)
