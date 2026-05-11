"""Pod routes — admin/lead management + member self-selection.

Path conventions (per ``docs/club/POD_OPERATIONS.md`` §"API surface"):

  * ``/admin/members/pods/*``      — admin (require_admin)
  * ``/members/pods/*``            — member-facing

All pod-related side effects (chat-channel ensure, member reconcile)
go through ``services.chat_sync`` so chat downtime never blocks pod flows.

Note: when these routes lived in sessions_service we resolved the
caller's member id over HTTP via ``get_member_by_auth_id``. Now that the
router lives in members_service, we resolve locally with a direct
SQLAlchemy query — same pattern used by ``routers/members.py``.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.models import (
    Member,
    Pod,
    PodAssignment,
    PodAssignmentSource,
    PodStatus,
)
from services.members_service.schemas.pod import (
    PodCreateRequest,
    PodDetail,
    PodMemberAddRequest,
    PodMemberOut,
    PodSummary,
    PodTransferRequest,
    PodUpdateRequest,
)
from services.members_service.services import pod_ops
from services.members_service.services.chat_sync import (
    ensure_pod_channel,
    reconcile_pod_membership,
)

logger = get_logger(__name__)

admin_router = APIRouter(prefix="/admin/members/pods", tags=["pods-admin"])
member_router = APIRouter(prefix="/members/pods", tags=["pods"])


async def _resolve_member_id(current_user: AuthUser, db: AsyncSession) -> uuid.UUID:
    """auth_id → member_id, resolved locally (we ARE members_service).

    Returns 403 if the caller doesn't have a member profile yet — a pod
    is a member-domain object; anonymous callers can't have one."""
    result = await db.execute(
        select(Member.id).where(Member.auth_id == current_user.user_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member profile not found",
        )
    return row[0]


async def _summary(db: AsyncSession, pod: Pod) -> PodSummary:
    return PodSummary.model_validate(await pod_ops.serialize_pod_summary(db, pod))


async def _detail(db: AsyncSession, pod: Pod) -> PodDetail:
    base = await pod_ops.serialize_pod_summary(db, pod)
    base["members"] = [
        PodMemberOut.model_validate(a) for a in pod.assignments if a.left_at is None
    ]
    return PodDetail.model_validate(base)


# ─── Admin ──────────────────────────────────────────────────────────


@admin_router.post("", response_model=PodSummary, status_code=status.HTTP_201_CREATED)
async def admin_create_pod(
    body: PodCreateRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_member_id(current_user, db)

    pod = await pod_ops.create_pod(
        db,
        club_id=body.club_id,
        name=body.name,
        handle=body.handle,
        description=body.description,
        pod_lead_id=body.pod_lead_id,
        assistant_pod_lead_id=body.assistant_pod_lead_id,
        min_size=body.min_size,
        max_size=body.max_size,
        default_session_day=body.default_session_day,
        default_session_time=body.default_session_time,
        default_session_duration_minutes=body.default_session_duration_minutes,
        default_pool_id=body.default_pool_id,
        visibility=body.visibility,
        created_by=actor_id,
    )

    # Provision chat channel; Pod Lead becomes channel admin via `created_by`.
    await ensure_pod_channel(
        pod_id=pod.id,
        pod_name=pod.handle or pod.name,
        pod_lead_id=pod.pod_lead_id,
    )

    return await _summary(db, pod)


@admin_router.get("/review-queue", response_model=List[PodSummary])
async def admin_review_queue(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Pods past their 3-month review window — admin/Pod Lead decides
    continue / rebalance / dissolve."""
    pods = await pod_ops.list_review_due(db)
    return [await _summary(db, p) for p in pods]


@admin_router.get("/{pod_id}", response_model=PodDetail)
async def admin_get_pod(
    pod_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pod = await pod_ops.get_pod_or_404(db, pod_id)
    return await _detail(db, pod)


@admin_router.patch("/{pod_id}", response_model=PodSummary)
async def admin_update_pod(
    pod_id: uuid.UUID,
    body: PodUpdateRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pod = await pod_ops.update_pod(
        db,
        pod_id=pod_id,
        fields=body.model_dump(exclude_unset=True),
    )
    return await _summary(db, pod)


@admin_router.post("/{pod_id}/dissolve", response_model=PodSummary)
async def admin_dissolve_pod(
    pod_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Dissolves the pod and soft-leaves every active member. Chat is
    NOT auto-archived from here — the chat admin API owns archival, and
    we don't want to baby-sit a side effect that may need a manual
    review trail. Admin archives the channel from chat admin once the
    final messages settle."""
    # Capture active assignments BEFORE dissolve so we can reconcile chat.
    # ``db.get(Pod, …)`` doesn't trigger the ``lazy="selectin"`` relationship
    # eagerly, so we query the assignments directly instead of going through
    # ``pod.assignments`` (which would lazy-load synchronously and explode
    # under the async driver with MissingGreenlet).
    pod = await pod_ops.get_pod_or_404(db, pod_id)
    active_result = await db.execute(
        select(PodAssignment).where(
            PodAssignment.pod_id == pod_id,
            PodAssignment.left_at.is_(None),
        )
    )
    active = list(active_result.scalars().all())
    # Snapshot the ids — after dissolve_pod commits and soft-leaves the
    # assignments, ``a.left_at`` will be set, but ``a.id`` and
    # ``a.member_id`` are simple columns and remain readable.
    snapshot = [(a.id, a.member_id) for a in active]

    pod = await pod_ops.dissolve_pod(db, pod_id=pod_id)

    for assignment_id, member_id in snapshot:
        await reconcile_pod_membership(
            pod_id=pod_id,
            member_id=member_id,
            assignment_id=assignment_id,
            action="remove",
        )

    return await _summary(db, pod)


@admin_router.post("/{pod_id}/extend", response_model=PodSummary)
async def admin_extend_pod(
    pod_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pod = await pod_ops.extend_review(db, pod_id=pod_id)
    return await _summary(db, pod)


@admin_router.post(
    "/{pod_id}/members",
    response_model=PodMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def admin_add_member(
    pod_id: uuid.UUID,
    body: PodMemberAddRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_member_id(current_user, db)
    pod = await pod_ops.get_pod_or_404(db, pod_id)
    assignment = await pod_ops.add_member(
        db,
        pod=pod,
        member_id=body.member_id,
        assigned_by=PodAssignmentSource.ADMIN,
        assigned_by_id=actor_id,
    )
    await reconcile_pod_membership(
        pod_id=pod.id,
        member_id=body.member_id,
        assignment_id=assignment.id,
        action="add",
    )
    return PodMemberOut.model_validate(assignment)


@admin_router.delete(
    "/{pod_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def admin_remove_member(
    pod_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    assignment = await pod_ops.remove_member(db, pod_id=pod_id, member_id=member_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member is not in this pod",
        )
    await reconcile_pod_membership(
        pod_id=pod_id,
        member_id=member_id,
        assignment_id=assignment.id,
        action="remove",
    )


@admin_router.post(
    "/{pod_id}/transfers",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def admin_transfer_member(
    pod_id: uuid.UUID,
    body: PodTransferRequest,
    member_id: uuid.UUID = Query(
        ..., description="Member being moved (kept in query so the body stays focused)"
    ),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_member_id(current_user, db)
    old_assignment, new_assignment = await pod_ops.transfer_member(
        db,
        source_pod_id=pod_id,
        target_pod_id=body.target_pod_id,
        member_id=member_id,
        actor_id=actor_id,
    )
    await reconcile_pod_membership(
        pod_id=pod_id,
        member_id=member_id,
        assignment_id=old_assignment.id,
        action="remove",
    )
    await reconcile_pod_membership(
        pod_id=body.target_pod_id,
        member_id=member_id,
        assignment_id=new_assignment.id,
        action="add",
    )


# ─── Member-facing ──────────────────────────────────────────────────


@member_router.get("/me", response_model=Optional[PodSummary])
async def get_my_pod(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """My current pod, or null if I'm not in one. Used by the dashboard."""
    member_id = await _resolve_member_id(current_user, db)
    pod = await pod_ops.get_my_pod(db, member_id=member_id)
    if pod is None:
        return None
    return await _summary(db, pod)


@member_router.get("/i-lead", response_model=List[PodSummary])
async def list_pods_i_lead(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Pods where I am the lead OR assistant lead.

    Powers two member-facing surfaces:
      * The "Pod Lead Review" entry in the member sidebar — only shown
        when this returns at least one pod.
      * The Pod-Lead-side challenge review queue, which uses the list
        for context (e.g. "you're reviewing as Pod Lead of {pod.name}").

    Returns ALL pods the member leads regardless of status, so a recently
    dissolved pod still shows up briefly before fading from the UI.
    """
    member_id = await _resolve_member_id(current_user, db)
    rows = await db.execute(
        select(Pod)
        .where(
            (Pod.pod_lead_id == member_id) | (Pod.assistant_pod_lead_id == member_id)
        )
        .order_by(Pod.created_at.desc())
    )
    pods = list(rows.scalars().all())
    return [await _summary(db, p) for p in pods]


@member_router.get("/public", response_model=List[PodSummary])
async def list_public_pods(
    club_id: Optional[uuid.UUID] = Query(default=None),
    db: AsyncSession = Depends(get_async_db),
):
    """Public pod directory — anonymous read.

    Returns only pods with visibility='public' (set on the row). Pods
    intentionally have a public-facing handle ("dolphins", "orcas") and
    no member PII is exposed in PodSummary — the response is metadata
    only (handle, lead/assistant UUIDs, capacity, schedule). Safe to
    expose to the unauthenticated /club marketing page.

    Private pods stay hidden — they go through admin assignment.
    """
    pods = await pod_ops.list_public_pods(db, club_id=club_id)
    return [await _summary(db, p) for p in pods]


@member_router.post(
    "/{pod_id}/join",
    response_model=PodMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def member_join_pod(
    pod_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Self-join a public pod with capacity. Refuses for private pods —
    those go through admin assignment."""
    member_id = await _resolve_member_id(current_user, db)
    pod = await pod_ops.get_pod_or_404(db, pod_id)

    if pod.visibility.value != "public":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This pod is private; ask an admin to add you",
        )
    if pod.status != PodStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pod is not active",
        )

    assignment = await pod_ops.add_member(
        db,
        pod=pod,
        member_id=member_id,
        assigned_by=PodAssignmentSource.SELF,
        assigned_by_id=None,
    )
    await reconcile_pod_membership(
        pod_id=pod.id,
        member_id=member_id,
        assignment_id=assignment.id,
        action="add",
    )
    return PodMemberOut.model_validate(assignment)


@member_router.post(
    "/me/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def member_leave_pod(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Leave my current pod. No-op if I'm not in one."""
    member_id = await _resolve_member_id(current_user, db)
    pod = await pod_ops.get_my_pod(db, member_id=member_id)
    if pod is None:
        return  # no-op

    assignment = await pod_ops.remove_member(db, pod_id=pod.id, member_id=member_id)
    if assignment is not None:
        await reconcile_pod_membership(
            pod_id=pod.id,
            member_id=member_id,
            assignment_id=assignment.id,
            action="remove",
        )
