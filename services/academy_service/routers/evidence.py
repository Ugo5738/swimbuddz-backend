"""Member-owned supplementary milestone videos, including completed cohorts.

Uploading evidence NEVER changes StudentProgress or a coach's verified assessment.
"""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client.core import internal_get
from libs.db.session import get_async_db
from services.academy_service.models import Enrollment, EnrollmentStatus, Milestone
from services.academy_service.models.evidence import MilestoneEvidence

router = APIRouter(tags=["academy-milestone-evidence"])


class MilestoneEvidenceCreate(BaseModel):
    milestone_id: uuid.UUID
    video_media_id: uuid.UUID
    kind: Literal["cohort_archive", "continued_progress"]
    caption: Optional[str] = Field(None, max_length=500)
    recorded_on: Optional[date] = None
    consent_to_share: bool = False

    @model_validator(mode="after")
    def recorded_date_is_not_future(self):
        if self.recorded_on and self.recorded_on > date.today():
            raise ValueError("The video recording date cannot be in the future")
        return self


class MilestoneEvidenceResponse(MilestoneEvidenceCreate):
    id: uuid.UUID
    enrollment_id: uuid.UUID
    approved_for_public: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


async def _own_enrollment(
    enrollment_id: uuid.UUID, current_user: AuthUser, db: AsyncSession
) -> Enrollment:
    enrollment = (
        await db.execute(select(Enrollment).where(Enrollment.id == enrollment_id))
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    if enrollment.member_auth_id != str(current_user.user_id):
        raise HTTPException(
            status_code=403, detail="This enrollment does not belong to you"
        )
    return enrollment


async def _validate_video_owner(media_id: uuid.UUID, auth_id: str) -> None:
    """Fail closed if the Media Service cannot verify the private upload."""
    try:
        resp = await internal_get(
            service_url=get_settings().MEDIA_SERVICE_URL,
            path=f"/internal/media/alumni-evidence/{media_id}",
            calling_service="academy",
            params={"owner_auth_id": auth_id},
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503, detail="Video verification is temporarily unavailable"
        ) from exc

    if resp.status_code == 404:
        raise HTTPException(
            status_code=400, detail="Upload your own milestone video first"
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=503, detail="Video verification is temporarily unavailable"
        )


@router.get(
    "/enrollments/{enrollment_id}/evidence",
    response_model=list[MilestoneEvidenceResponse],
)
async def list_milestone_evidence(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await _own_enrollment(enrollment_id, current_user, db)
    rows = await db.execute(
        select(MilestoneEvidence)
        .where(MilestoneEvidence.enrollment_id == enrollment_id)
        .order_by(MilestoneEvidence.created_at.desc(), MilestoneEvidence.id.desc())
    )
    return list(rows.scalars().all())


@router.post(
    "/enrollments/{enrollment_id}/evidence",
    response_model=MilestoneEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_milestone_evidence(
    enrollment_id: uuid.UUID,
    payload: MilestoneEvidenceCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    enrollment = await _own_enrollment(enrollment_id, current_user, db)
    if enrollment.status not in (EnrollmentStatus.ENROLLED, EnrollmentStatus.GRADUATED):
        raise HTTPException(
            status_code=403, detail="An active or graduated enrollment is required"
        )
    if enrollment.access_suspended:
        raise HTTPException(status_code=403, detail="Enrollment access is suspended")
    milestone = (
        await db.execute(select(Milestone).where(Milestone.id == payload.milestone_id))
    ).scalar_one_or_none()
    if milestone is None or milestone.program_id != enrollment.program_id:
        raise HTTPException(
            status_code=400, detail="Milestone does not belong to this enrollment"
        )

    await _validate_video_owner(payload.video_media_id, str(current_user.user_id))
    record = MilestoneEvidence(
        enrollment_id=enrollment_id,
        **payload.model_dump(),
        approved_for_public=False,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record
