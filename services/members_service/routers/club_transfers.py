"""Admin API for permanent Club home-location changes."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.schemas.club import (
    AdminClubEnrollmentSummary,
    ClubLocationTransferPreview,
    ClubLocationTransferRequest,
    ClubLocationTransferResponse,
)
from services.members_service.services.club_transfers import (
    execute_location_transfer,
    list_member_enrollments,
    preview_location_transfer,
)


router = APIRouter(prefix="/clubs/admin", tags=["clubs-admin-transfers"])


@router.get(
    "/members/{member_id}/enrollments",
    response_model=list[AdminClubEnrollmentSummary],
)
async def list_member_club_enrollments(
    member_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await list_member_enrollments(db, member_id)


@router.post(
    "/enrollments/{source_enrollment_id}/location-transfer/preview",
    response_model=ClubLocationTransferPreview,
)
async def preview_club_location_transfer(
    source_enrollment_id: uuid.UUID,
    body: ClubLocationTransferRequest,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await preview_location_transfer(db, source_enrollment_id, body)


@router.post(
    "/enrollments/{source_enrollment_id}/location-transfer",
    response_model=ClubLocationTransferResponse,
)
async def transfer_club_location(
    source_enrollment_id: uuid.UUID,
    body: ClubLocationTransferRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await execute_location_transfer(
        db,
        source_enrollment_id,
        body,
        requested_by_auth_id=admin.user_id,
    )
