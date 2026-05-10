"""Pod routes — admin/coach management + member self-selection.

Path conventions match the design doc (POD_MODEL_DESIGN.md §"API surface"):

  * `/admin/sessions/pods/*`      — admin / coach (require_admin)
  * `/sessions/pods/*`            — member-facing

All pod-related side effects (chat-channel ensure, member reconcile)
go through `services.chat_sync` so chat downtime never blocks pod flows.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db

from services.sessions_service.models import (
    Pod,
    PodAssignmentSource,
    PodStatus,
)
from services.sessions_service.schemas.pod import (
    PodCreateRequest,
    PodDetail,
    PodMemberAddRequest,
    PodMemberOut,
    PodSummary,
    PodTransferRequest,
    PodUpdateRequest,
)
from services.sessions_service.services import pod_ops
from services.sessions_service.services.chat_sync import (
    ensure_pod_channel,
    reconcile_pod_membership,
)

logger = get_logger(__name__)

admin_router = APIRouter(prefix="/admin/sessions/pods", tags=["pods-admin"])
member_router = APIRouter(prefix="/sessions/pods", tags=["pods"])


_CALLING_SERVICE = "sessions"


async def _resolve_member_id(current_user: AuthUser) -> uuid.UUID:
    """auth_id → members-service member_id. Mirrors the chat router pattern.

    Returns 403 if the caller doesn't have a member profile yet (a pod is a
    member-domain object — anonymous callers can't have one)."""
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service=_CALLING_SERVICE
    )
    if not member or "id" not in member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member profile not found",
        )
    return uuid.UUID(member["id"])


async def _summary(db: AsyncSession, pod: Pod) -> PodSummary:
    return PodSummary.model_validate(await pod_ops.serialize_pod_summary(db, pod))


async def _detail(db: AsyncSession, pod: Pod) -> PodDetail:
    base = await pod_ops.serialize_pod_summary(db, pod)
    base["members"] = [
        PodMemberOut.model_validate(a) for a in pod.assignments if a.left_at is None
    ]
    return PodDetail.model_validate(base)


# ─── Admin / coach ──────────────────────────────────────────────────


@admin_router.post("", response_model=PodSummary, status_code=status.HTTP_201_CREATED)
async def admin_create_pod(
    body: PodCreateRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_member_id(current_user)

    pod = await pod_ops.create_pod(
        db,
        club_id=body.club_id,
        name=body.name,
        description=body.description,
        lead_coach_id=body.lead_coach_id,
        assistant_coach_id=body.assistant_coach_id,
        min_size=body.min_size,
        max_size=body.max_size,
        visibility=body.visibility,
        created_by=actor_id,
    )

    # Provision chat channel; lead coach becomes channel admin via `created_by`.
    await ensure_pod_channel(
        pod_id=pod.id,
        pod_name=pod.name,
        lead_coach_id=pod.lead_coach_id,
    )

    return await _summary(db, pod)


@admin_router.get("/review-queue", response_model=List[PodSummary])
async def admin_review_queue(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Pods past their 3-month review window — admin/coach decides
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
    review trail. Coach archives the channel from chat admin once the
    final messages settle."""
    # Capture active membership BEFORE dissolve so we can reconcile chat.
    pod = await pod_ops.get_pod_or_404(db, pod_id)
    active = [a for a in pod.assignments if a.left_at is None]

    pod = await pod_ops.dissolve_pod(db, pod_id=pod_id)

    for a in active:
        await reconcile_pod_membership(
            pod_id=pod_id,
            member_id=a.member_id,
            assignment_id=a.id,
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
    actor_id = await _resolve_member_id(current_user)
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
    actor_id = await _resolve_member_id(current_user)
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
    member_id = await _resolve_member_id(current_user)
    pod = await pod_ops.get_my_pod(db, member_id=member_id)
    if pod is None:
        return None
    return await _summary(db, pod)


@member_router.get("/public", response_model=List[PodSummary])
async def list_public_pods(
    club_id: Optional[uuid.UUID] = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Public pod directory. Filter to a club if the caller knows which
    one they're registering for."""
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
    member_id = await _resolve_member_id(current_user)
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
    member_id = await _resolve_member_id(current_user)
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
