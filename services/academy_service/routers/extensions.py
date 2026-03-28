"""Cohort extension request router - coaches request, admins approve/reject."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin, require_coach
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortExtensionRequest,
    ExtensionRequestStatus,
)
from services.academy_service.schemas import (
    CohortExtensionRequestCreate,
    CohortExtensionRequestResponse,
    CohortExtensionRequestReview,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/extension-requests", tags=["extension-requests"])

MAX_EXTENSION_WEEKS = 4


@router.post(
    "/cohorts/{cohort_id}",
    response_model=CohortExtensionRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_extension_request(
    cohort_id: uuid.UUID,
    body: CohortExtensionRequestCreate,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """Coach requests an extension for a cohort they're assigned to."""
    # Get member for this coach
    member = await get_member_by_auth_id(current_user.user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Coach member profile not found")

    coach_member_id = uuid.UUID(member["id"])

    # Verify the cohort exists and coach is assigned
    cohort = await db.get(Cohort, cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if cohort.coach_id != coach_member_id:
        raise HTTPException(
            status_code=403,
            detail="You are not the assigned coach for this cohort",
        )

    # Check no pending request already exists for this cohort
    existing = await db.execute(
        select(CohortExtensionRequest).where(
            CohortExtensionRequest.cohort_id == cohort_id,
            CohortExtensionRequest.status == ExtensionRequestStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="A pending extension request already exists for this cohort",
        )

    # Validate weeks
    if body.weeks_requested < 1 or body.weeks_requested > MAX_EXTENSION_WEEKS:
        raise HTTPException(
            status_code=400,
            detail=f"Extension must be between 1 and {MAX_EXTENSION_WEEKS} weeks",
        )

    proposed_end_date = cohort.end_date + timedelta(weeks=body.weeks_requested)

    extension_request = CohortExtensionRequest(
        cohort_id=cohort_id,
        coach_id=coach_member_id,
        weeks_requested=body.weeks_requested,
        reason=body.reason,
        current_end_date=cohort.end_date,
        proposed_end_date=proposed_end_date,
        status=ExtensionRequestStatus.PENDING,
    )
    db.add(extension_request)
    await db.commit()
    await db.refresh(extension_request)

    logger.info(
        "Coach extension request created",
        extra={
            "extra_fields": {
                "cohort_id": str(cohort_id),
                "coach_id": str(coach_member_id),
                "weeks": body.weeks_requested,
            }
        },
    )

    return extension_request


@router.get(
    "/coach/me",
    response_model=list[CohortExtensionRequestResponse],
)
async def list_my_extension_requests(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List all extension requests created by the current coach."""
    member = await get_member_by_auth_id(current_user.user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Coach member profile not found")

    coach_member_id = uuid.UUID(member["id"])

    result = await db.execute(
        select(CohortExtensionRequest)
        .where(CohortExtensionRequest.coach_id == coach_member_id)
        .order_by(CohortExtensionRequest.created_at.desc())
    )
    return result.scalars().all()


@router.get(
    "/cohorts/{cohort_id}",
    response_model=list[CohortExtensionRequestResponse],
)
async def list_extension_requests_for_cohort(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all extension requests for a cohort (coach or admin)."""
    result = await db.execute(
        select(CohortExtensionRequest)
        .where(CohortExtensionRequest.cohort_id == cohort_id)
        .order_by(CohortExtensionRequest.created_at.desc())
    )
    return result.scalars().all()


@router.get(
    "/pending",
    response_model=list[CohortExtensionRequestResponse],
)
async def list_pending_extension_requests(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all pending extension requests (admin only)."""
    result = await db.execute(
        select(CohortExtensionRequest)
        .where(CohortExtensionRequest.status == ExtensionRequestStatus.PENDING)
        .order_by(CohortExtensionRequest.created_at.asc())
    )
    return result.scalars().all()


@router.post(
    "/{request_id}/approve",
    response_model=CohortExtensionRequestResponse,
)
async def approve_extension_request(
    request_id: uuid.UUID,
    body: CohortExtensionRequestReview,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin approves an extension request and updates the cohort end date."""
    ext_request = await db.get(CohortExtensionRequest, request_id)
    if not ext_request:
        raise HTTPException(status_code=404, detail="Extension request not found")

    if ext_request.status != ExtensionRequestStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Request has already been {ext_request.status.value}",
        )

    # Get admin member for audit
    admin_member = await get_member_by_auth_id(current_user.user_id)
    admin_member_id = uuid.UUID(admin_member["id"]) if admin_member else None

    # Update the cohort end date
    cohort = await db.get(Cohort, ext_request.cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    cohort.end_date = ext_request.proposed_end_date

    # Approve the request
    ext_request.status = ExtensionRequestStatus.APPROVED
    ext_request.reviewed_by_id = admin_member_id
    ext_request.admin_notes = body.admin_notes
    ext_request.reviewed_at = utc_now()

    await db.commit()
    await db.refresh(ext_request)

    logger.info(
        "Extension request approved",
        extra={
            "extra_fields": {
                "request_id": str(request_id),
                "cohort_id": str(ext_request.cohort_id),
                "new_end_date": str(ext_request.proposed_end_date),
            }
        },
    )

    return ext_request


@router.post(
    "/{request_id}/reject",
    response_model=CohortExtensionRequestResponse,
)
async def reject_extension_request(
    request_id: uuid.UUID,
    body: CohortExtensionRequestReview,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin rejects an extension request."""
    ext_request = await db.get(CohortExtensionRequest, request_id)
    if not ext_request:
        raise HTTPException(status_code=404, detail="Extension request not found")

    if ext_request.status != ExtensionRequestStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Request has already been {ext_request.status.value}",
        )

    admin_member = await get_member_by_auth_id(current_user.user_id)
    admin_member_id = uuid.UUID(admin_member["id"]) if admin_member else None

    ext_request.status = ExtensionRequestStatus.REJECTED
    ext_request.reviewed_by_id = admin_member_id
    ext_request.admin_notes = body.admin_notes
    ext_request.reviewed_at = utc_now()

    await db.commit()
    await db.refresh(ext_request)

    logger.info(
        "Extension request rejected",
        extra={
            "extra_fields": {
                "request_id": str(request_id),
                "cohort_id": str(ext_request.cohort_id),
            }
        },
    )

    return ext_request
