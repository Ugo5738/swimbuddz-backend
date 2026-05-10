"""Pod request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.sessions_service.models.enums import (
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
    description: Optional[str] = None
    lead_coach_id: uuid.UUID
    assistant_coach_id: Optional[uuid.UUID] = None
    visibility: PodVisibility
    status: PodStatus
    min_size: int
    max_size: int
    active_member_count: int
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
    server auto-names `{club_slug}-pod-{N}` (filled in by the service layer)."""

    club_id: uuid.UUID
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    lead_coach_id: uuid.UUID
    assistant_coach_id: Optional[uuid.UUID] = None
    min_size: int = Field(default=2, ge=1, le=20)
    max_size: int = Field(default=5, ge=1, le=20)
    visibility: PodVisibility = PodVisibility.PUBLIC


class PodUpdateRequest(BaseModel):
    """Partial update — admins can rename, change visibility, swap coaches,
    tune capacity. To extend the cycle, use the dedicated extend endpoint."""

    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    lead_coach_id: Optional[uuid.UUID] = None
    assistant_coach_id: Optional[uuid.UUID] = None
    min_size: Optional[int] = Field(default=None, ge=1, le=20)
    max_size: Optional[int] = Field(default=None, ge=1, le=20)
    visibility: Optional[PodVisibility] = None


class PodMemberAddRequest(BaseModel):
    member_id: uuid.UUID


class PodTransferRequest(BaseModel):
    """Coach moves a member from this pod to another pod (typically within
    the same Club)."""

    target_pod_id: uuid.UUID
