"""Rewards engine schemas — event ingestion, admin rules, stats."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Event Ingestion (internal, service-to-service)
# ---------------------------------------------------------------------------


class EventIngestRequest(BaseModel):
    """Payload sent by other services to trigger rewards processing."""

    event_id: uuid.UUID = Field(description="Source event ID for deduplication")
    event_type: str = Field(description="e.g. attendance.monthly_milestone")
    member_auth_id: str
    member_id: Optional[uuid.UUID] = None
    service_source: str = Field(description="Emitting service name")
    occurred_at: datetime = Field(description="When the event happened")
    event_data: dict = Field(default_factory=dict)
    idempotency_key: str


class RewardGrantItem(BaseModel):
    rule_name: str
    bubbles: int


class EventIngestResponse(BaseModel):
    event_id: uuid.UUID
    accepted: bool
    rewards_granted: int = 0
    rewards: list[RewardGrantItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reward Rules (admin)
# ---------------------------------------------------------------------------


class RewardRuleResponse(BaseModel):
    id: uuid.UUID
    rule_name: str
    display_name: str
    description: Optional[str] = None
    event_type: str
    trigger_config: Optional[dict] = None
    reward_bubbles: int
    reward_description_template: Optional[str] = None
    max_per_member_lifetime: Optional[int] = None
    max_per_member_per_period: Optional[int] = None
    period: Optional[str] = None
    replaces_rule_id: Optional[uuid.UUID] = None
    category: str
    is_active: bool
    priority: int
    requires_admin_confirmation: bool
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RewardRuleDetailResponse(RewardRuleResponse):
    """Extended response with usage stats."""

    total_grants: int = 0
    total_bubbles_distributed: int = 0


class RewardRuleCreateRequest(BaseModel):
    """Create a new reward rule."""

    rule_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique internal name, e.g. 'first_topup'",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable name shown to members",
    )
    description: Optional[str] = None
    event_type: str = Field(
        ..., min_length=1, description="Event type to trigger on, e.g. 'topup.first'"
    )
    trigger_config: Optional[dict] = Field(
        None, description="Optional JSON conditions (e.g. min_amount)"
    )
    reward_bubbles: int = Field(..., gt=0, description="Bubbles to grant per trigger")
    reward_description_template: Optional[str] = Field(
        None, description="Template with {amount} placeholder"
    )
    max_per_member_lifetime: Optional[int] = Field(None, ge=1)
    max_per_member_per_period: Optional[int] = Field(None, ge=1)
    period: Optional[str] = Field(None, description="day, week, month, or year")
    category: str = Field(
        ..., description="acquisition, retention, community, spending, or academy"
    )
    is_active: bool = True
    priority: int = Field(0, description="Higher = evaluated first")
    requires_admin_confirmation: bool = False


class RewardRuleUpdateRequest(BaseModel):
    """Partial update for a reward rule."""

    display_name: Optional[str] = None
    description: Optional[str] = None
    reward_bubbles: Optional[int] = Field(None, gt=0)
    reward_description_template: Optional[str] = None
    max_per_member_lifetime: Optional[int] = None
    max_per_member_per_period: Optional[int] = None
    period: Optional[str] = None
    requires_admin_confirmation: Optional[bool] = None
    is_active: Optional[bool] = None


class RewardRuleListResponse(BaseModel):
    items: list[RewardRuleResponse]
    total: int


# ---------------------------------------------------------------------------
# Admin Event Submission
# ---------------------------------------------------------------------------


class AdminEventSubmitRequest(BaseModel):
    """Admin submits a reward event on behalf of a member.

    Used for safety reports, content contributions, social shares,
    ride-share completions, and other ad-hoc reward triggers.
    """

    event_type: str = Field(
        description="Must match an active reward rule, e.g. safety.report_confirmed"
    )
    member_auth_id: str
    event_data: dict = Field(default_factory=dict)
    description: Optional[str] = Field(
        None, description="Admin notes about why this event is being submitted"
    )


# ---------------------------------------------------------------------------
# Reward Events (admin)
# ---------------------------------------------------------------------------


class RewardEventListItem(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    member_auth_id: str
    service_source: str
    processed: bool
    processed_at: Optional[datetime] = None
    rewards_granted: int
    processing_error: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RewardEventListResponse(BaseModel):
    items: list[RewardEventListItem]
    total: int


# ---------------------------------------------------------------------------
# Reward Stats (admin dashboard)
# ---------------------------------------------------------------------------


class EventTypeCount(BaseModel):
    event_type: str
    count: int


class TopRuleUsage(BaseModel):
    rule_name: str
    display_name: str
    total_grants: int
    total_bubbles: int


class RewardStatsResponse(BaseModel):
    total_rules_active: int
    total_events_processed: int
    total_events_pending: int
    total_bubbles_distributed: int
    events_by_type: list[EventTypeCount] = Field(default_factory=list)
    top_rules_by_usage: list[TopRuleUsage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Anti-Abuse Alerts (Phase 3d)
# ---------------------------------------------------------------------------


class RewardAlertResponse(BaseModel):
    id: uuid.UUID
    alert_type: str
    severity: str
    status: str
    member_auth_id: Optional[str] = None
    referral_code_id: Optional[uuid.UUID] = None
    title: str
    description: str
    alert_data: dict = Field(default_factory=dict)
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RewardAlertListResponse(BaseModel):
    items: list[RewardAlertResponse]
    total: int


class RewardAlertUpdateRequest(BaseModel):
    """Update alert status (acknowledge, resolve, dismiss)."""

    status: str = Field(description="open, acknowledged, resolved, dismissed")
    resolution_notes: Optional[str] = None


class AlertSummaryItem(BaseModel):
    status: str
    severity: str
    count: int


class RewardAlertSummaryResponse(BaseModel):
    total_open: int
    total_acknowledged: int
    total_resolved: int
    total_dismissed: int
    by_severity: list[AlertSummaryItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ambassador Badge (Phase 3d)
# ---------------------------------------------------------------------------


class AmbassadorStatusResponse(BaseModel):
    is_ambassador: bool
    successful_referrals: int
    referrals_to_ambassador: int = Field(
        description="Referrals needed to reach ambassador (0 if already ambassador)"
    )
    ambassador_since: Optional[datetime] = None
    total_referral_bubbles_earned: int


# ---------------------------------------------------------------------------
# Referral Leaderboard (Phase 3d)
# ---------------------------------------------------------------------------


class LeaderboardEntry(BaseModel):
    rank: int
    member_auth_id: str
    member_name: Optional[str] = None
    referral_code: str
    successful_referrals: int
    total_bubbles_earned: int
    conversion_rate: float = Field(description="Percentage of uses that qualified")


class ReferralLeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    period: str


# ---------------------------------------------------------------------------
# Notification Preferences (Phase 3d)
# ---------------------------------------------------------------------------


class NotificationPreferenceResponse(BaseModel):
    id: uuid.UUID
    member_auth_id: str
    notify_on_reward: bool
    notify_on_referral_qualified: bool
    notify_on_ambassador_milestone: bool
    notify_on_streak_milestone: bool
    notify_channel: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationPreferenceUpdateRequest(BaseModel):
    """Partial update — all fields optional."""

    notify_on_reward: Optional[bool] = None
    notify_on_referral_qualified: Optional[bool] = None
    notify_on_ambassador_milestone: Optional[bool] = None
    notify_on_streak_milestone: Optional[bool] = None
    notify_channel: Optional[str] = None


# ---------------------------------------------------------------------------
# Rewards Analytics (Phase 3d)
# ---------------------------------------------------------------------------


class RewardCategoryStats(BaseModel):
    category: str
    total_grants: int
    total_bubbles: int
    unique_members: int


class RewardAnalyticsResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    total_events: int
    total_rewards_granted: int
    total_bubbles_distributed: int
    unique_members_rewarded: int
    by_category: list[RewardCategoryStats] = Field(default_factory=list)
    avg_bubbles_per_member: float
    top_event_types: list[EventTypeCount] = Field(default_factory=list)
