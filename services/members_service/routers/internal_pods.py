"""Internal service-to-service endpoints for Pod reads.

Hosted under ``/internal/members/pods/*`` so other services
(``sessions_service`` in particular) can resolve a pod's identity,
schedule, and active members without sharing a database table.

Authenticated with service_role JWT only — never exposed via the
gateway. See docs/club/POD_OPERATIONS.md "Sessions service interaction"
section for usage.
"""

import uuid
from datetime import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import DayOfWeek, Pod, PodAssignment, PodStatus

router = APIRouter(prefix="/internal/members/pods", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas — slim, only what other services actually need.
# ---------------------------------------------------------------------------


class PodInternalSummary(BaseModel):
    """The shape sessions_service uses when scheduling a pod's sessions."""

    id: str
    club_id: str
    name: str
    slug: str
    handle: Optional[str] = None
    pod_lead_id: str
    assistant_pod_lead_id: Optional[str] = None
    status: str
    visibility: str
    min_size: int
    max_size: int
    active_member_count: int
    default_session_day: str
    default_session_time: str  # ISO HH:MM:SS
    default_session_duration_minutes: int
    default_pool_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=False)


class PodInternalDetail(PodInternalSummary):
    """Same as summary, plus the active member ids — used by sessions
    service when it needs to know "who should I create attendance rows
    for?"."""

    active_member_ids: List[str]


class PodRosterBatchRequest(BaseModel):
    pod_ids: List[uuid.UUID] = Field(default_factory=list, max_length=200)


class PodRosterBatchResponse(BaseModel):
    active_member_ids_by_pod: dict[str, List[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_time(t: time) -> str:
    return t.isoformat(timespec="seconds")


def _fmt_day(d: DayOfWeek) -> str:
    return d.value if isinstance(d, DayOfWeek) else str(d)


async def _active_count(db: AsyncSession, pod_id: uuid.UUID) -> int:
    from sqlalchemy import func

    result = await db.execute(
        select(func.count())
        .select_from(PodAssignment)
        .where(
            PodAssignment.pod_id == pod_id,
            PodAssignment.left_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def _active_member_ids(db: AsyncSession, pod_id: uuid.UUID) -> List[str]:
    result = await db.execute(
        select(PodAssignment.member_id).where(
            PodAssignment.pod_id == pod_id,
            PodAssignment.left_at.is_(None),
        )
    )
    return [str(mid) for mid in result.scalars().all()]


def _to_summary(pod: Pod, active_count: int) -> PodInternalSummary:
    return PodInternalSummary(
        id=str(pod.id),
        club_id=str(pod.club_id),
        name=pod.name,
        slug=pod.slug,
        handle=pod.handle,
        pod_lead_id=str(pod.pod_lead_id),
        assistant_pod_lead_id=(
            str(pod.assistant_pod_lead_id) if pod.assistant_pod_lead_id else None
        ),
        status=pod.status.value if hasattr(pod.status, "value") else str(pod.status),
        visibility=(
            pod.visibility.value
            if hasattr(pod.visibility, "value")
            else str(pod.visibility)
        ),
        min_size=pod.min_size,
        max_size=pod.max_size,
        active_member_count=active_count,
        default_session_day=_fmt_day(pod.default_session_day),
        default_session_time=_fmt_time(pod.default_session_time),
        default_session_duration_minutes=pod.default_session_duration_minutes,
        default_pool_id=str(pod.default_pool_id) if pod.default_pool_id else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/rosters/batch", response_model=PodRosterBatchResponse)
async def get_pod_rosters_batch_internal(
    payload: PodRosterBatchRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> PodRosterBatchResponse:
    """Return active member rosters for many pods in one database query."""
    pod_ids = list(dict.fromkeys(payload.pod_ids))
    rosters: dict[str, List[str]] = {str(pod_id): [] for pod_id in pod_ids}
    if not pod_ids:
        return PodRosterBatchResponse(active_member_ids_by_pod=rosters)

    rows = (
        await db.execute(
            select(PodAssignment.pod_id, PodAssignment.member_id).where(
                PodAssignment.pod_id.in_(pod_ids),
                PodAssignment.left_at.is_(None),
            )
        )
    ).all()
    for pod_id, member_id in rows:
        rosters[str(pod_id)].append(str(member_id))

    return PodRosterBatchResponse(active_member_ids_by_pod=rosters)


@router.get("/{pod_id}", response_model=PodInternalDetail)
async def get_pod_internal(
    pod_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Single pod lookup. Used by sessions_service when creating a
    Club session that's scoped to a specific pod — needs the schedule
    fields and the active member roster."""
    pod = await db.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found")

    active_ids = await _active_member_ids(db, pod_id)
    base = _to_summary(pod, len(active_ids))
    return PodInternalDetail(**base.model_dump(), active_member_ids=active_ids)


@router.get("", response_model=List[PodInternalSummary])
async def list_pods_internal(
    club_id: Optional[uuid.UUID] = Query(default=None),
    status: Optional[str] = Query(
        default=None,
        description="Filter by status (active|inactive). Defaults to active only.",
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """List pods. Used by sessions_service for batch scheduling — e.g.
    "create this Saturday's sessions for every active pod in club X"."""
    q = select(Pod)
    if club_id is not None:
        q = q.where(Pod.club_id == club_id)

    # Default to active-only; passing status=all bypasses the filter.
    if status is None:
        q = q.where(Pod.status == PodStatus.ACTIVE)
    elif status.lower() in {"active", "inactive"}:
        q = q.where(Pod.status == PodStatus(status.lower()))
    elif status.lower() != "all":
        raise HTTPException(
            status_code=400, detail="status must be 'active', 'inactive', or 'all'"
        )

    q = q.order_by(Pod.created_at.desc())
    result = await db.execute(q)
    pods = list(result.scalars().all())

    return [_to_summary(p, await _active_count(db, p.id)) for p in pods]
