"""Member-owned supplementary milestone videos, including completed cohorts.

Uploading evidence NEVER changes StudentProgress or a coach's verified assessment.
"""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    is_admin_or_service,
    require_admin,
    require_coach,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client.core import internal_get
from libs.db.session import get_async_db
from services.academy_service.models import Enrollment, EnrollmentStatus, Milestone
from services.academy_service.models.evidence import MilestoneEvidence
from services.academy_service.routers._shared import require_coach_for_cohort

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
    public_display_name: Optional[str] = None
    publication_consent_at: Optional[datetime] = None
    coach_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    showcase_review_notes: Optional[str] = None
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


class EvidenceReviewRequest(BaseModel):
    coach_notes: str = Field(..., min_length=2, max_length=2000)


class EvidenceShowcaseRequest(BaseModel):
    approve: bool
    review_notes: str = Field(..., min_length=2, max_length=2000)
    publication_consent_confirmed: bool = False


class EvidenceReviewResponse(BaseModel):
    evidence_id: uuid.UUID
    coach_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_for_public: bool
    showcase_review_notes: Optional[str] = None


@router.get("/admin/evidence", response_model=list[MilestoneEvidenceResponse])
async def list_admin_evidence(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rows = await db.execute(
        select(MilestoneEvidence).order_by(MilestoneEvidence.created_at.desc()).limit(200)
    )
    return list(rows.scalars().all())


@router.post("/evidence/{evidence_id}/coach-review", response_model=EvidenceReviewResponse)
async def review_evidence(
    evidence_id: uuid.UUID,
    payload: EvidenceReviewRequest,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    evidence = await db.get(MilestoneEvidence, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    enrollment = await db.get(Enrollment, evidence.enrollment_id)
    if not is_admin_or_service(current_user):
        if enrollment is None or enrollment.cohort_id is None:
            raise HTTPException(status_code=403, detail="Coach access required")
        await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
    evidence.coach_notes = payload.coach_notes
    evidence.reviewed_at = utc_now()
    await db.commit()
    return EvidenceReviewResponse(
        evidence_id=evidence.id,
        coach_notes=evidence.coach_notes,
        reviewed_at=evidence.reviewed_at,
        approved_for_public=evidence.approved_for_public,
        showcase_review_notes=evidence.showcase_review_notes,
    )


@router.post("/admin/evidence/{evidence_id}/showcase", response_model=EvidenceReviewResponse)
async def review_showcase(
    evidence_id: uuid.UUID,
    payload: EvidenceShowcaseRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    evidence = await db.get(MilestoneEvidence, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if payload.approve and not (
        evidence.publication_consent_at and payload.publication_consent_confirmed
    ):
        raise HTTPException(
            status_code=409,
            detail="Explicit publication consent must be verified before approving",
        )
    evidence.approved_for_public = payload.approve
    evidence.showcase_approved_at = utc_now() if payload.approve else None
    evidence.showcase_review_notes = payload.review_notes
    await db.commit()
    return EvidenceReviewResponse(
        evidence_id=evidence.id,
        coach_notes=evidence.coach_notes,
        reviewed_at=evidence.reviewed_at,
        approved_for_public=evidence.approved_for_public,
        showcase_review_notes=evidence.showcase_review_notes,
    )


class PublicationConsentRequest(BaseModel):
    consent: bool
    display_name: Optional[str] = Field(None, max_length=80)


@router.patch(
    "/enrollments/{enrollment_id}/evidence/{evidence_id}/publication-consent",
    response_model=MilestoneEvidenceResponse,
)
async def update_publication_consent(
    enrollment_id: uuid.UUID,
    evidence_id: uuid.UUID,
    payload: PublicationConsentRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Owners can withdraw publication immediately; approval must be redone."""
    await _own_enrollment(enrollment_id, current_user, db)
    evidence = await db.get(MilestoneEvidence, evidence_id)
    if evidence is None or evidence.enrollment_id != enrollment_id:
        raise HTTPException(status_code=404, detail="Evidence not found")
    evidence.publication_consent_at = utc_now() if payload.consent else None
    evidence.public_display_name = (
        payload.display_name.strip() or None
        if payload.consent and payload.display_name
        else None
    )
    evidence.approved_for_public = False
    evidence.showcase_approved_at = None
    await db.commit()
    await db.refresh(evidence)
    return evidence


class PublicShowcaseItem(BaseModel):
    id: uuid.UUID
    title: str
    display_name: str
    published_at: datetime


@router.get("/public/showcase", response_model=list[PublicShowcaseItem])
async def list_public_showcase(db: AsyncSession = Depends(get_async_db)):
    rows = await db.execute(
        select(MilestoneEvidence, Milestone.name)
        .join(Milestone, Milestone.id == MilestoneEvidence.milestone_id)
        .where(
            MilestoneEvidence.approved_for_public.is_(True),
            MilestoneEvidence.publication_consent_at.is_not(None),
            MilestoneEvidence.showcase_approved_at.is_not(None),
        )
        .order_by(MilestoneEvidence.showcase_approved_at.desc())
        .limit(50)
    )
    return [
        PublicShowcaseItem(
            id=evidence.id,
            title=name,
            display_name=evidence.public_display_name or "SwimBuddz swimmer",
            published_at=evidence.showcase_approved_at,
        )
        for evidence, name in rows
    ]


@router.get("/public/showcase/{evidence_id}/play")
async def play_showcase_video(
    evidence_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Check current consent on every playback, including after withdrawal."""
    evidence = await db.get(MilestoneEvidence, evidence_id)
    if not evidence or not (
        evidence.approved_for_public
        and evidence.publication_consent_at
        and evidence.showcase_approved_at
    ):
        raise HTTPException(status_code=404, detail="Story not available")
    try:
        response = await internal_get(
            service_url=get_settings().MEDIA_SERVICE_URL,
            path=f"/internal/media/alumni-evidence/{evidence.video_media_id}/public-playback",
            calling_service="academy",
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503, detail="Video temporarily unavailable"
        ) from exc
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Video temporarily unavailable")
    return RedirectResponse(
        response.json()["url"],
        status_code=307,
        headers={"Cache-Control": "no-store"},
    )
