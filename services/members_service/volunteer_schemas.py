"""Additional schemas for volunteer and challenge management."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ===== VOLUNTEER SCHEMAS =====
class VolunteerRoleBase(BaseModel):
    """Base volunteer role schema."""

    title: str
    description: Optional[str] = None
    category: str  # media/logistics/admin/coaching_support/lane_marshal
    slots_available: Optional[int] = None


class VolunteerRoleCreate(VolunteerRoleBase):
    """Schema for creating a volunteer role."""

    is_active: bool = True


class VolunteerRoleUpdate(BaseModel):
    """Schema for updating a volunteer role."""

    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    slots_available: Optional[int] = None
    is_active: Optional[bool] = None


class VolunteerRoleResponse(VolunteerRoleBase):
    """Volunteer role response schema."""

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
    interested_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


class VolunteerInterestCreate(BaseModel):
    """Schema for registering volunteer interest."""

    role_id: uuid.UUID
    notes: Optional[str] = None


class VolunteerInterestResponse(BaseModel):
    """Volunteer interest response schema."""

    id: uuid.UUID
    role_id: uuid.UUID
    member_id: uuid.UUID
    status: str  # interested/active/inactive
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== CHALLENGE SCHEMAS =====
class ClubChallengeBase(BaseModel):
    """Base club challenge schema."""

    title: str
    description: Optional[str] = None
    challenge_type: str  # time_trial/attendance/distance/technique
    badge_name: str
    criteria_json: Optional[dict] = None


class ClubChallengeCreate(ClubChallengeBase):
    """Schema for creating a club challenge."""

    is_active: bool = True


class ClubChallengeUpdate(BaseModel):
    """Schema for updating a club challenge."""

    title: Optional[str] = None
    description: Optional[str] = None
    challenge_type: Optional[str] = None
    badge_name: Optional[str] = None
    criteria_json: Optional[dict] = None
    is_active: Optional[bool] = None


class ClubChallengeResponse(ClubChallengeBase):
    """Club challenge response schema."""

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
    completion_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


class ChallengeCompletionCreate(BaseModel):
    """Schema for marking a challenge as complete."""

    challenge_id: uuid.UUID
    member_id: uuid.UUID
    result_data: Optional[dict] = None


class ChallengeCompletionResponse(BaseModel):
    """Challenge completion response schema."""

    id: uuid.UUID
    member_id: uuid.UUID
    challenge_id: uuid.UUID
    completed_at: datetime
    result_data: Optional[dict] = None
    verified_by: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
