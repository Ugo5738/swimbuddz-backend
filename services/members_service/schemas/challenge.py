"""Schemas for the volunteer (legacy) and club challenge surfaces.

The challenges section was reshaped in Phase 1 of the challenges revamp:
  * ClubChallenge gains audience, format, scoping (club/cohort), schedule,
    rewards, team config, public-visibility flags, and example media.
  * Submissions are first-class with a pending → approved | rejected lifecycle,
    proof media, optional team rosters, and per-member reward ledger fields.
  * Two new admin-side schemas (ChallengeSubmissionReview) and one
    public-friendly read schema for the landing-page surface.
"""

import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ===== VOLUNTEER SCHEMAS (legacy) =====
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


# ===== SHARED ENUMS / LITERALS =====

ChallengeType = Literal["time_trial", "attendance", "distance", "technique"]
ChallengeAudience = Literal["community", "club", "academy", "all"]
ChallengeFormat = Literal["participatory", "competition"]
SubmissionStatus = Literal["pending", "approved", "rejected"]


# ===== CHALLENGE EXAMPLE MEDIA =====


class ChallengeExampleMediaItem(BaseModel):
    """One example media reference, sent on create/update of a challenge."""

    media_id: uuid.UUID
    order_idx: int = 0
    caption: Optional[str] = None


class ChallengeExampleMediaResponse(ChallengeExampleMediaItem):
    """Example media as returned by the API (with id + URLs hydrated by caller)."""

    id: uuid.UUID
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ===== CLUB CHALLENGE SCHEMAS =====


class ClubChallengeBase(BaseModel):
    """Common fields shared by create / update / response.

    Per-instance scoping (club_id, academy_cohort_id) is optional and additive
    to `audience`. e.g. audience='club' + club_id=NULL means "any club";
    audience='club' + club_id=X means "only club X".
    """

    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None  # stringified BlockNote JSON
    challenge_type: ChallengeType
    badge_name: str
    reward_badge_image_media_id: Optional[uuid.UUID] = None

    # Configurable rewards
    reward_bubbles_amount: Optional[int] = Field(default=None, ge=0)
    reward_volunteer_hours: Optional[float] = Field(default=None, ge=0)

    criteria_json: Optional[dict] = None  # legacy

    # Audience + scoping
    audience: ChallengeAudience = "all"
    club_id: Optional[uuid.UUID] = None
    academy_cohort_id: Optional[uuid.UUID] = None

    # Format + schedule
    format: ChallengeFormat = "participatory"
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

    # Visibility
    is_public: bool = True
    show_winner_media_publicly: bool = True

    # Team config
    team_enabled: bool = False
    team_min_size: Optional[int] = Field(default=None, ge=1)
    team_max_size: Optional[int] = Field(default=None, ge=1)


class ClubChallengeCreate(ClubChallengeBase):
    """Schema for creating a club challenge."""

    is_active: bool = True
    example_media: List[ChallengeExampleMediaItem] = Field(default_factory=list)


class ClubChallengeUpdate(BaseModel):
    """Schema for updating a club challenge (all fields optional)."""

    title: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    challenge_type: Optional[ChallengeType] = None
    badge_name: Optional[str] = None
    reward_badge_image_media_id: Optional[uuid.UUID] = None
    reward_bubbles_amount: Optional[int] = Field(default=None, ge=0)
    reward_volunteer_hours: Optional[float] = Field(default=None, ge=0)
    criteria_json: Optional[dict] = None
    audience: Optional[ChallengeAudience] = None
    club_id: Optional[uuid.UUID] = None
    academy_cohort_id: Optional[uuid.UUID] = None
    format: Optional[ChallengeFormat] = None
    winner_submission_id: Optional[uuid.UUID] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    show_winner_media_publicly: Optional[bool] = None
    team_enabled: Optional[bool] = None
    team_min_size: Optional[int] = Field(default=None, ge=1)
    team_max_size: Optional[int] = Field(default=None, ge=1)
    example_media: Optional[List[ChallengeExampleMediaItem]] = None


class ClubChallengeResponse(ClubChallengeBase):
    """Club challenge response schema."""

    id: uuid.UUID
    is_active: bool
    winner_submission_id: Optional[uuid.UUID] = None
    completion_count: Optional[int] = 0  # approved-only count
    submission_count: Optional[int] = 0  # all statuses
    example_media: List[ChallengeExampleMediaResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== SUBMISSION SCHEMAS =====


class ChallengeSubmissionMediaItem(BaseModel):
    """One proof media reference, attached to a submission on create."""

    media_id: uuid.UUID
    order_idx: int = 0


class ChallengeSubmissionMediaResponse(ChallengeSubmissionMediaItem):
    id: uuid.UUID
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ChallengeSubmissionCreate(BaseModel):
    """Member-facing payload to submit an attempt at a challenge.

    The captain (current user) is added automatically to the team roster on
    the server; `team_member_ids` should contain the OTHER teammates only.
    """

    challenge_id: uuid.UUID
    submission_note: Optional[str] = None
    proof_media: List[ChallengeSubmissionMediaItem] = Field(default_factory=list)
    team_member_ids: List[uuid.UUID] = Field(default_factory=list)
    # legacy / parity with the prior endpoint shape
    result_data: Optional[dict] = None


class ChallengeSubmissionReview(BaseModel):
    """Admin-only payload to approve or reject a submission."""

    status: Literal["approved", "rejected"]
    review_note: Optional[str] = None


class ChallengeSubmissionMemberResponse(BaseModel):
    """One row from the per-member ledger on a submission."""

    id: uuid.UUID
    member_id: uuid.UUID
    member_name: Optional[str] = None  # full name when available; admin-facing
    role: Optional[str] = None
    badge_awarded: bool = False
    bubbles_grant_id: Optional[uuid.UUID] = None
    volunteer_hours_log_id: Optional[uuid.UUID] = None
    rewarded_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ChallengeSubmissionResponse(BaseModel):
    """Submission as returned to admins / the submitter themselves."""

    id: uuid.UUID
    challenge_id: uuid.UUID
    challenge_title: Optional[str] = None  # convenience for review-queue tables
    member_id: uuid.UUID
    member_name: Optional[str] = None  # captain's full name (admin-facing)
    submitted_by_member_id: Optional[uuid.UUID] = None
    submission_note: Optional[str] = None
    is_team_submission: bool = False
    status: SubmissionStatus = "pending"
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[uuid.UUID] = None
    review_note: Optional[str] = None
    rewards_distributed_at: Optional[datetime] = None
    completed_at: datetime
    result_data: Optional[dict] = None
    proof_media: List[ChallengeSubmissionMediaResponse] = Field(default_factory=list)
    members: List[ChallengeSubmissionMemberResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== LEGACY ALIASES (kept so the old route signatures still import) =====


class ChallengeCompletionCreate(BaseModel):
    """Legacy completion-create shape used by the admin "mark complete" path.

    The new submission flow uses ChallengeSubmissionCreate; this alias is kept
    so the existing admin `mark_challenge_complete` endpoint continues to work
    while the broader submission flow lands.
    """

    challenge_id: uuid.UUID
    member_id: uuid.UUID
    result_data: Optional[dict] = None


class ChallengeCompletionResponse(BaseModel):
    """Legacy completion-response (subset of ChallengeSubmissionResponse)."""

    id: uuid.UUID
    member_id: uuid.UUID
    challenge_id: uuid.UUID
    completed_at: datetime
    result_data: Optional[dict] = None
    verified_by: Optional[uuid.UUID] = None
    status: SubmissionStatus = "pending"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== PUBLIC SURFACE =====


class ChallengeWinnerPublicInfo(BaseModel):
    """Winner summary shown on the public landing page.

    Only populated when format='competition' AND winner_submission_id is set.
    Member emails / auth_ids are NEVER included — only display names.
    proof_media is only populated when show_winner_media_publicly is true on
    the challenge.
    """

    submission_id: uuid.UUID
    captain_name: str
    teammate_names: List[str] = Field(default_factory=list)
    is_team_submission: bool = False
    proof_media: List[ChallengeSubmissionMediaResponse] = Field(default_factory=list)


class ChallengePublicResponse(BaseModel):
    """Public-facing challenge summary.

    Excludes admin-internal fields (club_id, academy_cohort_id, is_active,
    is_public, criteria_json, winner_submission_id raw). Suitable for
    rendering on the unauthenticated landing-page surface.
    """

    id: uuid.UUID
    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    challenge_type: ChallengeType
    badge_name: str
    reward_badge_image_media_id: Optional[uuid.UUID] = None
    badge_image_url: Optional[str] = None
    reward_bubbles_amount: Optional[int] = None
    reward_volunteer_hours: Optional[float] = None
    audience: ChallengeAudience
    format: ChallengeFormat
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    team_enabled: bool
    team_min_size: Optional[int] = None
    team_max_size: Optional[int] = None
    completion_count: int = 0
    example_media: List[ChallengeExampleMediaResponse] = Field(default_factory=list)
    winner: Optional[ChallengeWinnerPublicInfo] = None
    is_finished: bool = False
    created_at: datetime


# ===== BADGE AWARDS =====


class ChallengeBadgeAwardResponse(BaseModel):
    """A single earned badge as shown on a member's profile."""

    id: uuid.UUID
    member_id: uuid.UUID
    challenge_id: uuid.UUID
    submission_id: Optional[uuid.UUID] = None
    badge_name: str
    badge_image_media_id: Optional[uuid.UUID] = None
    badge_image_url: Optional[str] = None  # hydrated by caller via media_service
    awarded_at: datetime

    model_config = ConfigDict(from_attributes=True)
