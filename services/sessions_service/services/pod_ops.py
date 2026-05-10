"""Pod operations: create, update, dissolve, member assignment, transfer.

Capacity, "one active pod per member", and slug uniqueness are all
enforced here (with the DB partial-unique index as defence in depth).
HTTP routers stay thin — they orchestrate auth + call into these helpers.
"""

import re
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger

from services.sessions_service.models import (
    Pod,
    PodAssignment,
    PodAssignmentSource,
    PodStatus,
    PodVisibility,
)

logger = get_logger(__name__)

# Pods run on a Club's 3-month training cycle. Stored as days so we can
# nudge the schedule for unusual seasons without reaching for a calendar
# library.
_REVIEW_CYCLE_DAYS = 90


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """URL-safe pod slug. Doesn't have to be perfect; uniqueness is
    enforced per-club at the DB level so collisions just retry."""
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "pod"


async def _next_pod_number(db: AsyncSession, club_id: uuid.UUID) -> int:
    """Used to auto-name pods (`pod-1`, `pod-2`, …) when admin doesn't
    supply a name. Counts every pod ever created in the club, including
    dissolved — guarantees a stable, monotonically growing label."""
    result = await db.execute(
        select(func.count()).select_from(Pod).where(Pod.club_id == club_id)
    )
    return int(result.scalar() or 0) + 1


async def _active_member_count(db: AsyncSession, pod_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(PodAssignment)
        .where(
            PodAssignment.pod_id == pod_id,
            PodAssignment.left_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def get_pod_or_404(db: AsyncSession, pod_id: uuid.UUID) -> Pod:
    pod = await db.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pod not found"
        )
    return pod


# ─── Create / update / dissolve ──────────────────────────────────────


async def create_pod(
    db: AsyncSession,
    *,
    club_id: uuid.UUID,
    name: Optional[str],
    description: Optional[str],
    lead_coach_id: uuid.UUID,
    assistant_coach_id: Optional[uuid.UUID],
    min_size: int,
    max_size: int,
    visibility: PodVisibility,
    created_by: uuid.UUID,
) -> Pod:
    if min_size < 1 or max_size < min_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_size must be >= 1 and <= max_size",
        )

    if not name:
        n = await _next_pod_number(db, club_id)
        name = f"pod-{n}"
    slug = _slugify(name)

    now = utc_now()
    pod = Pod(
        club_id=club_id,
        name=name,
        slug=slug,
        description=description,
        lead_coach_id=lead_coach_id,
        assistant_coach_id=assistant_coach_id,
        min_size=min_size,
        max_size=max_size,
        visibility=visibility,
        status=PodStatus.ACTIVE,
        cycle_started_at=now,
        review_due_at=now + timedelta(days=_REVIEW_CYCLE_DAYS),
        created_by=created_by,
    )
    db.add(pod)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Slug clash within the same club — retry once with a numeric suffix.
        await db.rollback()
        if "uq_pods_club_slug" not in str(exc):
            raise
        suffix = await _next_pod_number(db, club_id)
        pod.slug = f"{slug}-{suffix}"
        db.add(pod)
        await db.commit()

    await db.refresh(pod)
    return pod


async def update_pod(
    db: AsyncSession,
    *,
    pod_id: uuid.UUID,
    fields: dict,
) -> Pod:
    pod = await get_pod_or_404(db, pod_id)
    if pod.status == PodStatus.INACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a dissolved pod",
        )

    if "min_size" in fields and "max_size" in fields:
        if fields["max_size"] < fields["min_size"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_size must be >= min_size",
            )

    for k, v in fields.items():
        if v is not None:
            setattr(pod, k, v)

    pod.updated_at = utc_now()
    await db.commit()
    await db.refresh(pod)
    return pod


async def dissolve_pod(db: AsyncSession, *, pod_id: uuid.UUID) -> Pod:
    """Mark inactive and soft-leave every active member.

    Chat-channel archive happens out-of-band (the chat admin API or a
    follow-up task)."""
    pod = await get_pod_or_404(db, pod_id)
    if pod.status == PodStatus.INACTIVE:
        return pod  # idempotent

    now = utc_now()
    pod.status = PodStatus.INACTIVE
    pod.dissolved_at = now

    # Soft-leave all active members so the chat-sync remove path fires
    # for each (router calls reconcile per assignment after this returns).
    result = await db.execute(
        select(PodAssignment).where(
            PodAssignment.pod_id == pod_id,
            PodAssignment.left_at.is_(None),
        )
    )
    for a in result.scalars().all():
        a.left_at = now

    await db.commit()
    await db.refresh(pod)
    return pod


async def extend_review(db: AsyncSession, *, pod_id: uuid.UUID) -> Pod:
    """Resets the review window — coach/admin chose to continue this pod
    for another 3 months."""
    pod = await get_pod_or_404(db, pod_id)
    if pod.status == PodStatus.INACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot extend a dissolved pod",
        )
    now = utc_now()
    pod.cycle_started_at = now
    pod.review_due_at = now + timedelta(days=_REVIEW_CYCLE_DAYS)
    await db.commit()
    await db.refresh(pod)
    return pod


# ─── Membership ──────────────────────────────────────────────────────


async def _ensure_member_has_no_active_pod(
    db: AsyncSession, member_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(PodAssignment).where(
            PodAssignment.member_id == member_id,
            PodAssignment.left_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Member is already in an active pod — leave or transfer first",
        )


async def add_member(
    db: AsyncSession,
    *,
    pod: Pod,
    member_id: uuid.UUID,
    assigned_by: PodAssignmentSource,
    assigned_by_id: Optional[uuid.UUID],
) -> PodAssignment:
    if pod.status != PodStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot join a dissolved pod",
        )

    count = await _active_member_count(db, pod.id)
    if count >= pod.max_size:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pod is full",
        )

    await _ensure_member_has_no_active_pod(db, member_id)

    assignment = PodAssignment(
        pod_id=pod.id,
        member_id=member_id,
        assigned_by=assigned_by,
        assigned_by_id=assigned_by_id,
    )
    db.add(assignment)
    try:
        await db.commit()
    except IntegrityError:
        # Partial-unique index race — another request just added them.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Member is already in an active pod",
        ) from None

    await db.refresh(assignment)
    return assignment


async def remove_member(
    db: AsyncSession,
    *,
    pod_id: uuid.UUID,
    member_id: uuid.UUID,
) -> Optional[PodAssignment]:
    """Soft-leave the active assignment for this member in this pod.
    Returns the row that was modified (so callers can pass its id to
    chat reconcile), or None if the member wasn't actively in the pod."""
    result = await db.execute(
        select(PodAssignment).where(
            and_(
                PodAssignment.pod_id == pod_id,
                PodAssignment.member_id == member_id,
                PodAssignment.left_at.is_(None),
            )
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        return None
    assignment.left_at = utc_now()
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def transfer_member(
    db: AsyncSession,
    *,
    source_pod_id: uuid.UUID,
    target_pod_id: uuid.UUID,
    member_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> tuple[PodAssignment, PodAssignment]:
    """Coach moves a member from one pod to another.

    Returns (old_assignment, new_assignment) so the router can call chat
    reconcile twice — `remove` from old, `add` to new."""
    if source_pod_id == target_pod_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source and target pods are the same",
        )

    target = await get_pod_or_404(db, target_pod_id)

    # Pull the source assignment first so we can soft-leave it.
    src_result = await db.execute(
        select(PodAssignment).where(
            PodAssignment.pod_id == source_pod_id,
            PodAssignment.member_id == member_id,
            PodAssignment.left_at.is_(None),
        )
    )
    src = src_result.scalar_one_or_none()
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member is not in the source pod",
        )

    # Capacity check on target before we commit the leave (so a "full"
    # error doesn't strand the member with no pod).
    count = await _active_member_count(db, target.id)
    if count >= target.max_size:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target pod is full",
        )

    src.left_at = utc_now()
    new_assignment = PodAssignment(
        pod_id=target.id,
        member_id=member_id,
        assigned_by=PodAssignmentSource.COACH_TRANSFER,
        assigned_by_id=actor_id,
    )
    db.add(new_assignment)
    await db.commit()
    await db.refresh(src)
    await db.refresh(new_assignment)
    return src, new_assignment


# ─── Read helpers ────────────────────────────────────────────────────


async def serialize_pod_summary(db: AsyncSession, pod: Pod) -> dict:
    """Build the dict that fits PodSummary — adds the computed
    `active_member_count`."""
    return {
        "id": pod.id,
        "club_id": pod.club_id,
        "name": pod.name,
        "slug": pod.slug,
        "description": pod.description,
        "lead_coach_id": pod.lead_coach_id,
        "assistant_coach_id": pod.assistant_coach_id,
        "visibility": pod.visibility,
        "status": pod.status,
        "min_size": pod.min_size,
        "max_size": pod.max_size,
        "active_member_count": await _active_member_count(db, pod.id),
        "cycle_started_at": pod.cycle_started_at,
        "review_due_at": pod.review_due_at,
        "dissolved_at": pod.dissolved_at,
        "created_at": pod.created_at,
        "updated_at": pod.updated_at,
    }


async def list_public_pods(
    db: AsyncSession, *, club_id: Optional[uuid.UUID]
) -> list[Pod]:
    """Public directory query — public + active pods only."""
    q = select(Pod).where(
        Pod.visibility == PodVisibility.PUBLIC,
        Pod.status == PodStatus.ACTIVE,
    )
    if club_id is not None:
        q = q.where(Pod.club_id == club_id)
    q = q.order_by(Pod.created_at.desc())
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_my_pod(db: AsyncSession, *, member_id: uuid.UUID) -> Optional[Pod]:
    """The member's currently active pod (one or none)."""
    result = await db.execute(
        select(Pod)
        .join(PodAssignment, PodAssignment.pod_id == Pod.id)
        .where(
            PodAssignment.member_id == member_id,
            PodAssignment.left_at.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_review_due(db: AsyncSession) -> list[Pod]:
    """Pods past their review-due date — admin/coach attention queue."""
    now = utc_now()
    result = await db.execute(
        select(Pod)
        .where(
            Pod.status == PodStatus.ACTIVE,
            Pod.review_due_at <= now,
        )
        .order_by(Pod.review_due_at.asc())
    )
    return list(result.scalars().all())
