"""Admin moderation routes for member pool submissions.

Approving a submission:
  1. Creates a Pool row (partnership_status=prospect, is_active=True)
  2. Marks the submission as approved and links promoted_pool_id
  3. Grants Bubbles to the submitter via HTTP call to wallet_service (no DB coupling)

Rejecting a submission:
  1. Marks the submission as rejected with admin review notes (no Pool created)
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import grant_pool_submission_reward
from libs.db.session import get_async_db
from services.pools_service.models import (
    PartnershipStatus,
    Pool,
    PoolSubmission,
    PoolSubmissionStatus,
)
from services.pools_service.schemas import (
    PoolSubmissionApproveRequest,
    PoolSubmissionListResponse,
    PoolSubmissionRejectRequest,
    PoolSubmissionResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-pool-submissions"])


def _slugify(text: str) -> str:
    """Simple slug generator — lowercase, hyphen-separated alphanumerics."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug or "pool"


@router.get("", response_model=PoolSubmissionListResponse)
async def list_submissions(
    submission_status: Optional[PoolSubmissionStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List pool submissions with optional status filter."""
    base = select(PoolSubmission)
    if submission_status is not None:
        base = base.where(PoolSubmission.status == submission_status)

    count = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    rows = (
        (
            await db.execute(
                base.order_by(PoolSubmission.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return PoolSubmissionListResponse(
        items=rows, total=count, page=page, page_size=page_size
    )


@router.get("/{submission_id}", response_model=PoolSubmissionResponse)
async def get_submission(
    submission_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    row = (
        await db.execute(
            select(PoolSubmission).where(PoolSubmission.id == submission_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return row


@router.post("/{submission_id}/approve", response_model=PoolSubmissionResponse)
async def approve_submission(
    submission_id: uuid.UUID,
    body: PoolSubmissionApproveRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a submission: promote to Pool and grant Bubbles to submitter."""
    submission = (
        await db.execute(
            select(PoolSubmission).where(PoolSubmission.id == submission_id)
        )
    ).scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != PoolSubmissionStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Submission is already {submission.status.value}",
        )

    # 1. Create Pool row (prospect status)
    base_slug = _slugify(submission.pool_name)
    # Ensure slug uniqueness by appending a short suffix on collision
    slug = base_slug
    suffix = 0
    while True:
        existing = (
            await db.execute(select(Pool).where(Pool.slug == slug))
        ).scalar_one_or_none()
        if not existing:
            break
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    pool = Pool(
        name=submission.pool_name,
        slug=slug,
        location_area=submission.location_area,
        contact_phone=submission.contact_phone,
        contact_email=submission.contact_email,
        has_changing_rooms=submission.has_changing_rooms,
        has_showers=submission.has_showers,
        has_lockers=submission.has_lockers,
        has_parking=submission.has_parking,
        has_lifeguard=submission.has_lifeguard,
        pool_type=submission.pool_type,
        notes=(
            f"Submitted by member ({submission.submitter_display_name or submission.submitter_auth_id}). "
            f"Address: {submission.address or 'n/a'}. Member notes: {submission.member_notes or 'n/a'}"
        ),
        partnership_status=PartnershipStatus.PROSPECT,
        is_active=True,
    )
    db.add(pool)
    await db.flush()  # get pool.id before commit

    # 2. Update submission
    submission.status = PoolSubmissionStatus.APPROVED
    submission.reviewed_by_auth_id = current_user.user_id
    submission.reviewed_at = utc_now()
    submission.review_notes = body.review_notes
    submission.promoted_pool_id = pool.id

    # 3. Grant Bubbles reward via HTTP to wallet_service (best-effort)
    if body.reward_bubbles and body.reward_bubbles > 0:
        try:
            grant = await grant_pool_submission_reward(
                member_auth_id=submission.submitter_auth_id,
                bubbles_amount=body.reward_bubbles,
                submission_id=str(submission.id),
                granted_by=current_user.user_id or "admin",
                calling_service="pools",
            )
            submission.reward_granted = True
            submission.reward_bubbles = body.reward_bubbles
            grant_id = grant.get("id") if isinstance(grant, dict) else None
            submission.reward_grant_id = grant_id
            logger.info(
                "Granted %d Bubbles for pool submission %s to %s",
                body.reward_bubbles,
                submission.id,
                submission.submitter_auth_id,
            )
        except Exception as exc:  # noqa: BLE001 — approval succeeds even if reward fails
            logger.error(
                "Failed to grant Bubbles for submission %s: %s",
                submission.id,
                exc,
            )
            # Leave reward_granted=False; admin can retry via a re-grant endpoint later.

    await db.commit()
    await db.refresh(submission)
    return submission


@router.post("/{submission_id}/reject", response_model=PoolSubmissionResponse)
async def reject_submission(
    submission_id: uuid.UUID,
    body: PoolSubmissionRejectRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a submission with review notes."""
    submission = (
        await db.execute(
            select(PoolSubmission).where(PoolSubmission.id == submission_id)
        )
    ).scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != PoolSubmissionStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Submission is already {submission.status.value}",
        )

    submission.status = PoolSubmissionStatus.REJECTED
    submission.reviewed_by_auth_id = current_user.user_id
    submission.reviewed_at = utc_now()
    submission.review_notes = body.review_notes

    await db.commit()
    await db.refresh(submission)
    return submission
