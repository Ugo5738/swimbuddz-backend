"""Response schemas for the internal members-service routes.

Kept slim — only what cross-service callers actually need.
"""

from typing import List, Optional

from pydantic import BaseModel


class MemberBasic(BaseModel):
    id: str
    # Supabase auth user UUID. Cross-service callers (academy, payments)
    # need this to call the members-service activation endpoints — those
    # are keyed by auth_id, not member_id. Endpoint is gated by
    # require_service_role, so exposing auth_id internally is safe.
    # Added 2026-05-17 after the academy mark-paid handler silently
    # skipped tier activation when this field was missing from the
    # response (admin_payments.py:264 sets `member_auth_id = member_data
    # .get("auth_id")` then guards on it).
    auth_id: str | None = None
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    community_paid_until: str | None = None
    profile_photo_url: str | None = None


class CoachProfileBasic(BaseModel):
    member_id: str
    status: str
    academy_cohort_stipend: int | None = None
    one_to_one_rate_per_hour: int | None = None
    group_session_rate_per_hour: int | None = None


class CoachBankAccountResponse(BaseModel):
    id: str
    member_id: str
    bank_code: str
    bank_name: str | None = None
    account_number: str
    account_name: str | None = None
    is_verified: bool
    recipient_code: str | None = None


class MemberMembershipResponse(BaseModel):
    member_id: str
    primary_tier: str
    active_tiers: list[str] | None = None
    community_paid_until: str | None = None
    club_paid_until: str | None = None
    academy_paid_until: str | None = None


class BulkMembersRequest(BaseModel):
    ids: List[str]


class EligibleCoachBasic(BaseModel):
    member_id: str
    name: str
    email: str
    grade: str | None = None
    total_coaching_hours: int = 0
    average_feedback_rating: float | None = None


class CoachReadinessData(BaseModel):
    """Extended coach profile data for readiness assessment."""

    profile_id: str
    total_coaching_hours: int = 0
    average_rating: float | None = None
    background_check_status: str | None = None
    has_cpr_training: bool = False
    cpr_expiry_date: Optional[str] = None
    has_active_agreement: bool = False


# ---------------------------------------------------------------------------
# Flywheel / funnel reporting schemas
# ---------------------------------------------------------------------------


class JoinedTierMember(BaseModel):
    id: str
    source_joined_at: str
    acquisition_source: str | None = None


class JoinedTierResponse(BaseModel):
    members: List[JoinedTierMember]


class TierHistoryEntry(BaseModel):
    tier: str
    entered_at: str
    exited_at: str | None = None


class TierHistoryResponse(BaseModel):
    entries: List[TierHistoryEntry]


class MemberSearchResult(BaseModel):
    """Slim search result with auth_id for cross-service filtering."""

    id: str
    auth_id: str
    first_name: str
    last_name: str
    email: str


class ApprovedMemberBasic(BaseModel):
    id: str
    auth_id: str
    first_name: str
    last_name: str
    primary_tier: str | None = None


class BirthdayMember(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    age: int


class AdminMember(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    roles: list[str]
