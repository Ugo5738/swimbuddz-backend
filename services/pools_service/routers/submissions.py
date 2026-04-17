"""Member-facing pool submission routes.

Any authenticated member can suggest a pool. The submission enters a
moderation queue; admins approve (which promotes to Pool + grants Bubbles)
or reject it. Cross-service communication (members lookup, Bubble grant)
uses HTTP — no DB coupling.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.pools_service.models import PoolSubmission, PoolSubmissionStatus
from services.pools_service.schemas import (
    PoolSubmissionCreate,
    PoolSubmissionListResponse,
    PoolSubmissionResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["pool-submissions"])


@router.post(
    "",
    response_model=PoolSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    payload: PoolSubmissionCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Submit a pool suggestion. Enters moderation queue as 'pending'."""
    # Enrich with submitter identity (best-effort — name/email aren't required).
    display_name: Optional[str] = None
    submitter_email: Optional[str] = current_user.email
    try:
        member = await get_member_by_auth_id(
            current_user.user_id, calling_service="pools"
        )
        if member:
            first = member.get("first_name") or ""
            last = member.get("last_name") or ""
            display_name = f"{first} {last}".strip() or None
            submitter_email = member.get("email") or submitter_email
    except Exception as exc:  # noqa: BLE001 — not fatal
        logger.warning("Could not resolve submitter identity: %s", exc)

    submission = PoolSubmission(
        submitter_auth_id=current_user.user_id,
        submitter_display_name=display_name,
        submitter_email=submitter_email,
        pool_name=payload.pool_name,
        location_area=payload.location_area,
        address=payload.address,
        pool_type=payload.pool_type,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        has_changing_rooms=payload.has_changing_rooms,
        has_showers=payload.has_showers,
        has_lockers=payload.has_lockers,
        has_parking=payload.has_parking,
        has_lifeguard=payload.has_lifeguard,
        visit_frequency=payload.visit_frequency,
        member_rating=payload.member_rating,
        member_notes=payload.member_notes,
        photo_url=payload.photo_url,
        status=PoolSubmissionStatus.PENDING,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)
    logger.info(
        "Pool submission created: %s by %s", submission.id, current_user.user_id
    )
    return submission


@router.get("/mine", response_model=PoolSubmissionListResponse)
async def list_my_submissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List the current member's submissions (all statuses)."""
    base = select(PoolSubmission).where(
        PoolSubmission.submitter_auth_id == current_user.user_id
    )
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
async def get_my_submission(
    submission_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get one of your own submissions."""
    row = (
        await db.execute(
            select(PoolSubmission).where(
                PoolSubmission.id == submission_id,
                PoolSubmission.submitter_auth_id == current_user.user_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return row
