"""Legacy completions endpoints (kept for back-compat with the admin form).

The new submission flow lives in `submissions.py` (POST
`/challenges/{id}/submissions`) + the review endpoint in
`admin_submissions.py` (PATCH `/challenges/submissions/{id}`).
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.members_service.models import (
    ChallengeSubmissionMember,
    ClubChallenge,
    MemberChallengeCompletion,
)
from services.members_service.schemas import (
    ChallengeCompletionCreate,
    ChallengeCompletionResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import (
    _admin_uuid_or_none,
    _award_badge_and_members,
    _distribute_external_rewards,
    _notify_submission_reviewed,
)

router = APIRouter()


@router.post(
    "/completions", response_model=ChallengeCompletionResponse, status_code=201
)
async def mark_challenge_complete(
    completion_data: ChallengeCompletionCreate,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """LEGACY: admin records a pre-approved completion for a member.

    Kept so existing admin tooling continues to work. The new submission
    flow lives at POST /challenges/{id}/submissions + PATCH /submissions/{id}.

    This path creates a submission already in `approved` status with the
    admin recorded as the reviewer, writes a per-member roster row, and
    produces a badge award. Multiple "approved" rows for the same member
    on the same challenge are blocked here (use a fresh challenge instead).
    """
    import json

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == completion_data.challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Block creating a duplicate approved completion (the badge ledger
    # would dedupe anyway, but giving a clear 400 keeps clients honest).
    existing = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.challenge_id == completion_data.challenge_id,
            MemberChallengeCompletion.member_id == completion_data.member_id,
            MemberChallengeCompletion.status == "approved",
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Challenge already approved for this member",
        )

    admin_uuid = _admin_uuid_or_none(admin)

    completion = MemberChallengeCompletion(
        challenge_id=completion_data.challenge_id,
        member_id=completion_data.member_id,
        submitted_by_member_id=completion_data.member_id,
        result_data=(
            json.dumps(completion_data.result_data)
            if completion_data.result_data
            else None
        ),
        status="approved",
        verified_by=admin_uuid,
        reviewed_by=admin_uuid,
        reviewed_by_kind="admin",  # legacy admin path is HQ-only by definition
        reviewed_at=utc_now(),
    )
    db.add(completion)
    await db.flush()

    db.add(
        ChallengeSubmissionMember(
            submission_id=completion.id,
            member_id=completion_data.member_id,
        )
    )

    await _award_badge_and_members(completion, challenge, db)
    completion.rewards_distributed_at = utc_now()

    # First commit — persists the local approval before firing
    # cross-service grants (same pattern as review_challenge_submission).
    await db.commit()
    await db.refresh(completion)

    # Best-effort cross-service Bubbles + volunteer-hours grants. Failures
    # are logged; the approval still succeeds.
    await _distribute_external_rewards(
        completion,
        challenge,
        db,
        granted_by_auth=admin.user_id,
    )
    await db.commit()
    await db.refresh(completion)

    # Member-facing notification (fire-and-forget).
    await _notify_submission_reviewed(
        completion,
        challenge,
        db,
        status="approved",
        review_note=None,
    )

    completion_dict = {
        column.name: getattr(completion, column.name)
        for column in completion.__table__.columns
    }
    completion_dict["result_data"] = (
        json.loads(completion.result_data) if completion.result_data else None
    )
    return ChallengeCompletionResponse.model_validate(completion_dict)


@router.get(
    "/{challenge_id}/completions", response_model=List[ChallengeCompletionResponse]
)
async def list_challenge_completions(
    challenge_id: uuid.UUID,
    status: Optional[str] = Query(
        None, description="Filter by status: pending|approved|rejected"
    ),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List submissions for a specific challenge (admin only).

    Returns the legacy ChallengeCompletionResponse shape for back-compat.
    For the richer per-member + media view, use /submissions/pending or
    a future GET /challenges/{id}/submissions endpoint.
    """
    import json

    query = select(MemberChallengeCompletion).where(
        MemberChallengeCompletion.challenge_id == challenge_id
    )
    if status:
        query = query.where(MemberChallengeCompletion.status == status)
    query = query.order_by(MemberChallengeCompletion.completed_at.desc())

    result = await db.execute(query)
    completions = result.scalars().all()

    completions_list = []
    for completion in completions:
        completion_dict = {
            column.name: getattr(completion, column.name)
            for column in completion.__table__.columns
        }
        completion_dict["result_data"] = (
            json.loads(completion.result_data) if completion.result_data else None
        )
        completions_list.append(
            ChallengeCompletionResponse.model_validate(completion_dict)
        )

    return completions_list
