"""Pod request/response schemas."""

import uuid
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.members_service.models.enums import (
    DayOfWeek,
    PodAssignmentSource,
    PodStatus,
    PodVisibility,
)


# ─── Read shapes ──────────────────────────────────────────────────────


class PodMemberOut(BaseModel):
    """One active assignment row."""

    id: uuid.UUID
    member_id: uuid.UUID
    joined_at: datetime
    assigned_by: PodAssignmentSource

    model_config = ConfigDict(from_attributes=True)


class PodSummary(BaseModel):
    """Compact pod shape used in list views (admin queue, public directory).

    Capacity counters are computed (not columns) — derived from the active
    assignments at read time."""

    id: uuid.UUID
    club_id: uuid.UUID
    name: str
    slug: str
    handle: Optional[str] = None
    description: Optional[str] = None
    pod_lead_id: uuid.UUID
    assistant_pod_lead_id: Optional[uuid.UUID] = None
    visibility: PodVisibility
    status: PodStatus
    min_size: int
    max_size: int
    active_member_count: int
    default_session_day: DayOfWeek
    default_session_time: time
    default_session_duration_minutes: int
    default_pool_id: Optional[uuid.UUID] = None
    cycle_started_at: datetime
    review_due_at: datetime
    dissolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PodDetail(PodSummary):
    """Full pod view — includes the current assignment list."""

    members: list[PodMemberOut] = Field(default_factory=list)


# ─── Write shapes ─────────────────────────────────────────────────────


class PodCreateRequest(BaseModel):
    """Admin creates a pod for a Club. `name` is optional — if blank, the
    server auto-names ``{club_slug}-pod-{N}``. Schedule fields are also
    optional — they inherit the parent Club's defaults when blank."""

    club_id: uuid.UUID
    name: Optional[str] = Field(default=None, max_length=120)
    handle: Optional[str] = Field(default=None, max_length=60)
    description: Optional[str] = Field(default=None, max_length=2000)
    pod_lead_id: uuid.UUID
    assistant_pod_lead_id: Optional[uuid.UUID] = None
    min_size: int = Field(default=2, ge=1, le=10)
    max_size: int = Field(default=5, ge=1, le=10)
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None
    visibility: PodVisibility = PodVisibility.PUBLIC


class PodUpdateRequest(BaseModel):
    """Partial update — admins can rename, change visibility, swap leads,
    tune capacity, override schedule. To extend the cycle, use the
    dedicated extend endpoint."""

    name: Optional[str] = Field(default=None, max_length=120)
    handle: Optional[str] = Field(default=None, max_length=60)
    description: Optional[str] = Field(default=None, max_length=2000)
    pod_lead_id: Optional[uuid.UUID] = None
    assistant_pod_lead_id: Optional[uuid.UUID] = None
    min_size: Optional[int] = Field(default=None, ge=1, le=10)
    max_size: Optional[int] = Field(default=None, ge=1, le=10)
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None
    visibility: Optional[PodVisibility] = None


class PodMemberAddRequest(BaseModel):
    member_id: uuid.UUID


class PodTransferRequest(BaseModel):
    """Pod Lead / admin moves a member from this pod to another pod
    (typically within the same Club)."""

    target_pod_id: uuid.UUID
