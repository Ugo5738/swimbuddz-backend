"""Member Swim Scorecard HTTP routes (ADR-026).

Mounted at `/scorecards` in `members_service` and proxied at
`/api/v1/scorecards/*` by `gateway_service`.
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.schemas.scorecard import (
    BatchBenchmarkDayRequest,
    BatchBenchmarkDayResponse,
    BenchmarkItemResponse,
    CoachCreateBenchmarkRequest,
    CreateBenchmarkRequest,
    MemberScorecardResponse,
    PeerScorecardHighlightResponse,
    PodScorecardOverviewResponse,
    PublicShareScorecardResponse,
    ReviewBenchmarkRequest,
    ScorecardReviewQueueResponse,
    ScorecardRosterMemberSummary,
    ScorecardSettingsResponse,
    ScorecardSkillItem,
    SelfUpdateSkillRequest,
    UpdateScorecardSettingsRequest,
    VerifySkillRequest,
)
from services.members_service.services import scorecard_service
from services.members_service.services.scorecard_catalog import serialize_catalog

router = APIRouter(prefix="/scorecards", tags=["scorecards"])


# ── Catalog & Public Share ────────────────────────────────────────────────


@router.get("/catalog")
async def get_scorecard_catalog():
    """Return canonical SwimBuddz skill domains, skills, strokes & benchmark specs."""
    return serialize_catalog()


@router.get("/public/{share_token}", response_model=PublicShareScorecardResponse)
async def get_public_shared_scorecard(
    share_token: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Strictly opt-in public positive milestone card (no authentication required)."""
    return await scorecard_service.build_public_share_card(db, share_token)


# ── Self Member Scorecard ─────────────────────────────────────────────────


@router.get("/me", response_model=MemberScorecardResponse)
async def get_my_scorecard(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the authenticated member's complete live Swim Scorecard."""
    member = await scorecard_service.get_member_by_auth_or_404(db, current_user.user_id)
    return await scorecard_service.build_member_scorecard(
        db,
        member,
        viewer_audience="self",
        viewer_user=current_user,
    )


@router.put("/me/skills/{skill_code}", response_model=ScorecardSkillItem)
async def update_my_skill(
    skill_code: str,
    payload: SelfUpdateSkillRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Self-update proficiency on a canonical swim skill and optionally request review."""
    member = await scorecard_service.get_member_by_auth_or_404(db, current_user.user_id)
    return await scorecard_service.upsert_self_skill(
        db,
        member,
        skill_code,
        proficiency=payload.proficiency,
        member_notes=payload.member_notes,
        request_verification=payload.request_verification,
    )


@router.post(
    "/me/benchmarks",
    response_model=BenchmarkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_benchmark(
    payload: CreateBenchmarkRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Log a personal swim benchmark (progressive Club gating on advanced metrics)."""
    member = await scorecard_service.get_member_by_auth_or_404(db, current_user.user_id)
    return await scorecard_service.create_self_benchmark(
        db, member, current_user, payload
    )


@router.delete(
    "/me/benchmarks/{benchmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_my_benchmark(
    benchmark_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete an unverified self-logged benchmark owned by the current member."""
    member = await scorecard_service.get_member_by_auth_or_404(db, current_user.user_id)
    await scorecard_service.delete_self_benchmark(db, member, benchmark_id)
    return None


@router.patch("/me/settings", response_model=ScorecardSettingsResponse)
async def update_my_scorecard_settings(
    payload: UpdateScorecardSettingsRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update audience visibility toggles, preferred pool length, or share token."""
    member = await scorecard_service.get_member_by_auth_or_404(db, current_user.user_id)
    return await scorecard_service.update_member_settings(db, member, payload)


# ── Coach / Pod Lead / Admin Operations ───────────────────────────────────


@router.get("/review-queue", response_model=ScorecardReviewQueueResponse)
async def list_scorecard_review_queue(
    pod_id: Optional[uuid.UUID] = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Pending skill claims and benchmark verifications scoped by caller role."""
    return await scorecard_service.get_review_queue(db, current_user, pod_id=pod_id)


@router.get("/roster", response_model=List[ScorecardRosterMemberSummary])
async def list_scorecard_roster(
    search: Optional[str] = Query(default=None),
    pod_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Searchable swimmer roster for Coaches, Admins, and active Pod Leads."""
    return await scorecard_service.list_scorecard_roster(
        db,
        current_user,
        search=search,
        pod_id=pod_id,
        limit=limit,
    )


@router.post(
    "/benchmark-day/batch",
    response_model=BatchBenchmarkDayResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_benchmark_day_batch(
    payload: BatchBenchmarkDayRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Batch-record Coach/Pod-Lead verified benchmarks during a Club Benchmark Day."""
    return await scorecard_service.record_batch_benchmark_day(db, current_user, payload)


@router.post("/benchmarks/{benchmark_id}/review", response_model=BenchmarkItemResponse)
async def review_member_benchmark(
    benchmark_id: uuid.UUID,
    payload: ReviewBenchmarkRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve (`coach_verified`) or reject a member's self-logged benchmark."""
    return await scorecard_service.review_benchmark(
        db,
        benchmark_id,
        current_user=current_user,
        action=payload.action,
        verifier_notes=payload.verifier_notes,
    )


# ── Pod Overview & Audience-Scoped Member Scorecard ───────────────────────


@router.get("/pod/{pod_id}", response_model=PodScorecardOverviewResponse)
async def get_pod_scorecard_overview(
    pod_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Pod roster highlights honoring each swimmer's `pod_visibility_enabled` setting."""
    return await scorecard_service.get_pod_scorecard_overview(db, current_user, pod_id)


@router.get(
    "/members/{member_id}",
    response_model=Union[MemberScorecardResponse, PeerScorecardHighlightResponse],
)
async def get_member_scorecard_by_id(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Audience-scoped scorecard read (full for self/coach/admin/pod_lead; peer highlight for pod_mate)."""
    target_member = await scorecard_service.get_member_by_id_or_404(db, member_id)
    audience = await scorecard_service.resolve_viewer_audience(
        db, current_user, target_member
    )
    if audience == "pod_mate":
        return await scorecard_service.build_peer_highlight(db, target_member)
    return await scorecard_service.build_member_scorecard(
        db,
        target_member,
        viewer_audience=audience,
        viewer_user=current_user,
    )


@router.post(
    "/members/{member_id}/skills/{skill_code}/verify",
    response_model=ScorecardSkillItem,
)
async def verify_swimmer_skill(
    member_id: uuid.UUID,
    skill_code: str,
    payload: VerifySkillRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Coach, Admin, or scoped Pod Lead verifies or rejects a swimmer's skill."""
    verifier_member, verifier_role = await scorecard_service.assert_can_verify_target(
        db, current_user, member_id
    )
    target_member = await scorecard_service.get_member_by_id_or_404(db, member_id)
    return await scorecard_service.verify_member_skill(
        db,
        target_member,
        skill_code,
        action=payload.action,
        proficiency=payload.proficiency,
        verifier_member=verifier_member,
        verifier_role=verifier_role,
        verifier_notes=payload.verifier_notes,
    )


@router.post(
    "/members/{member_id}/benchmarks",
    response_model=BenchmarkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_verified_benchmark_for_swimmer(
    member_id: uuid.UUID,
    payload: CoachCreateBenchmarkRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Coach, Admin, or scoped Pod Lead directly records a verified benchmark."""
    verifier_member, verifier_role = await scorecard_service.assert_can_verify_target(
        db, current_user, member_id
    )
    target_member = await scorecard_service.get_member_by_id_or_404(db, member_id)
    return await scorecard_service.create_verified_benchmark(
        db,
        target_member,
        payload,
        verifier_member=verifier_member,
        verifier_role=verifier_role,
        verifier_notes=payload.verifier_notes,
    )
