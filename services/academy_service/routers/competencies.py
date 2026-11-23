"""Portable Academy competency passport: verified legacy evidence, not automatic credit.

This sidecar deliberately does NOT alter legacy StudentProgress, certificates,
Club eligibility, rewards or enrollment commercial terms.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client.core import internal_get
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    Enrollment,
    MilestoneEventType,
    MilestoneReviewEvent,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.services.requirements_resolver import (
    requirements_sha256,
)

router = APIRouter(prefix="/competencies", tags=["academy-competency-passport"])


class PromoteLegacyReviewInput(BaseModel):
    source_progress_id: uuid.UUID
    milestone_version_id: uuid.UUID
    criteria_passed: list[str] = Field(min_length=1)
    equivalence_reason: str = Field(min_length=30, max_length=4000)


class RevokeVerificationInput(BaseModel):
    reason: str = Field(min_length=20, max_length=4000)


def _view_verification(record, state: str, *, reason: str | None = None) -> dict:
    return {
        "id": str(record.id),
        "member_id": str(record.member_id),
        "milestone_definition_id": str(record.milestone_definition_id),
        "milestone_version_id": str(record.milestone_version_id),
        "source_enrollment_id": str(record.source_enrollment_id),
        "source_progress_id": str(record.source_progress_id),
        "source_review_event_id": str(record.source_review_event_id),
        "evidence_media_id": str(record.evidence_media_id),
        "verified_at": record.verified_at,
        "expires_at": record.expires_at,
        "status": state,
        "status_reason": reason,
        "criteria_passed": record.criteria_passed,
        "assessment_context_snapshot": record.assessment_context_snapshot,
    }


async def _latest_decision(db: AsyncSession, progress_id: uuid.UUID):
    return (
        await db.execute(
            select(MilestoneReviewEvent)
            .where(
                MilestoneReviewEvent.progress_id == progress_id,
                MilestoneReviewEvent.event_type.in_(
                    [
                        MilestoneEventType.APPROVED,
                        MilestoneEventType.REJECTED,
                        MilestoneEventType.OVERRIDE,
                        MilestoneEventType.STATUS_CHANGED,
                    ]
                ),
            )
            .order_by(
                MilestoneReviewEvent.created_at.desc(),
                MilestoneReviewEvent.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _source_review(db: AsyncSession, progress: StudentProgress):
    if progress.status != ProgressStatus.ACHIEVED:
        raise HTTPException(status_code=409, detail="Source milestone is not achieved")
    if progress.reviewed_at is None:
        raise HTTPException(
            status_code=409, detail="Source milestone has not been reviewed"
        )
    review = await _latest_decision(db, progress.id)
    if (
        review is None
        or review.new_status != ProgressStatus.ACHIEVED
        or review.event_type
        not in (MilestoneEventType.APPROVED, MilestoneEventType.OVERRIDE)
        or review.actor_role not in ("coach", "admin")
    ):
        raise HTTPException(
            status_code=409, detail="A human-approved source review is required"
        )
    if review.evidence_media_id_snapshot is None:
        raise HTTPException(
            status_code=409, detail="The source review did not verify video evidence"
        )
    if review.evidence_media_id_snapshot != progress.evidence_media_id:
        raise HTTPException(status_code=409, detail="Source evidence has changed")
    return review


async def _uploader_auth_id(
    db: AsyncSession, progress: StudentProgress, enrollment: Enrollment
):
    last_claim = (
        await db.execute(
            select(MilestoneReviewEvent)
            .where(
                MilestoneReviewEvent.progress_id == progress.id,
                MilestoneReviewEvent.event_type == MilestoneEventType.CLAIMED,
                MilestoneReviewEvent.evidence_media_id_snapshot
                == progress.evidence_media_id,
            )
            .order_by(
                MilestoneReviewEvent.created_at.desc(), MilestoneReviewEvent.id.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_claim is None:
        raise HTTPException(status_code=409, detail="Evidence provenance is missing")
    if last_claim.actor_role == "coach":
        # Source CLAIMED events identify the original coach uploader.
        return str(last_claim.actor_id)
    if last_claim.actor_role != "student" or not enrollment.member_auth_id:
        raise HTTPException(status_code=409, detail="Evidence owner cannot be verified")
    return str(enrollment.member_auth_id)


async def _check_media(progress: StudentProgress, owner_auth_id: str):
    try:
        response = await internal_get(
            service_url=get_settings().MEDIA_SERVICE_URL,
            path=f"/internal/media/alumni-evidence/{progress.evidence_media_id}",
            calling_service="academy",
            params={
                "owner_auth_id": owner_auth_id,
                "expected_linked_id": f"{progress.enrollment_id}:{progress.milestone_id}",
            },
        )
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(
            status_code=503, detail="Media verification temporarily unavailable"
        ) from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=422 if response.status_code == 404 else 503,
            detail="Source video evidence could not be verified",
        )


@router.post("/admin/verify-legacy", status_code=201)
async def verify_legacy_competency(
    payload: PromoteLegacyReviewInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role == "service_role":
        raise HTTPException(
            status_code=403, detail="Human administrator approval is required"
        )
    progress = (
        await db.execute(
            select(StudentProgress)
            .where(StudentProgress.id == payload.source_progress_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if progress is None:
        raise HTTPException(status_code=404, detail="Source milestone not found")

    enrollment = await db.get(Enrollment, progress.enrollment_id)
    if (
        enrollment is None
        or enrollment.cohort_id is None
        or not enrollment.member_auth_id
    ):
        raise HTTPException(
            status_code=409, detail="Verifiable enrollment and cohort required"
        )
    review = await _source_review(db, progress)

    version = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    if version is None or version.state != "published" or version.published_at is None:
        raise HTTPException(
            status_code=409, detail="Published milestone version required"
        )
    definition = await db.get(AcademyMilestoneDefinition, version.definition_id)
    if definition is None:
        raise HTTPException(status_code=409, detail="Missing canonical definition")
    if version.evidence_policy not in ("video", "time_trial"):
        raise HTTPException(
            status_code=409,
            detail="Portable practical credit requires video-based evidence",
        )

    snapshot = (
        await db.execute(
            select(AcademyCohortRequirementsSnapshot).where(
                AcademyCohortRequirementsSnapshot.cohort_id == enrollment.cohort_id
            )
        )
    ).scalar_one_or_none()
    if snapshot is None or snapshot.program_id != enrollment.program_id:
        raise HTTPException(
            status_code=409, detail="A frozen cohort assessment standard is required"
        )
    if requirements_sha256(snapshot.requirements_json) != snapshot.sha256:
        raise HTTPException(
            status_code=409, detail="Cohort requirements integrity mismatch"
        )

    match = next(
        (
            item
            for item in snapshot.requirements_json.get("milestones", [])
            if item.get("version_id") == str(version.id)
        ),
        None,
    )
    if match is None or match.get("rubric_snapshot") != version.rubric_json:
        raise HTTPException(
            status_code=409,
            detail="This version is not in the cohort's frozen requirements",
        )

    valid_days = version.assessment_context_json.get("validity_days")
    if valid_days is not None and (
        isinstance(valid_days, bool)
        or not isinstance(valid_days, int)
        or valid_days <= 0
    ):
        raise HTTPException(
            status_code=409, detail="Invalid assessment validity policy"
        )
    if version.safety_gate and valid_days is None:
        raise HTTPException(
            status_code=409, detail="Safety-sensitive credit needs a recheck interval"
        )
    rubric_criteria = version.rubric_json.get("criteria")
    if not isinstance(rubric_criteria, list) or not rubric_criteria:
        raise HTTPException(
            status_code=409, detail="Competency rubric needs explicit criteria"
        )
    expected = [
        item if isinstance(item, str) else item.get("code")
        for item in rubric_criteria
        if isinstance(item, (str, dict))
    ]
    if (
        len(expected) != len(rubric_criteria)
        or any(not isinstance(item, str) or not item for item in expected)
        or len(set(expected)) != len(expected)
        or len(set(payload.criteria_passed)) != len(payload.criteria_passed)
        or set(payload.criteria_passed) != set(expected)
    ):
        raise HTTPException(
            status_code=422,
            detail="Every published criterion must be explicitly attested",
        )

    already = (
        await db.execute(
            select(AcademyCompetencyVerification).where(
                AcademyCompetencyVerification.source_review_event_id == review.id,
                AcademyCompetencyVerification.milestone_version_id == version.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(status_code=409, detail="Source review already credited")

    uploader = await _uploader_auth_id(db, progress, enrollment)
    await _check_media(progress, uploader)
    # The source review is deliberately locked by the progress row; concurrent
    # admin operations on the same source progress serialize here.
    current_review = await _source_review(db, progress)
    if current_review.id != review.id:
        raise HTTPException(
            status_code=409, detail="Source review changed during verification"
        )

    # Validity starts when the swimmer passed the source assessment, not
    # when an administrator later imports that evidence into the passport.
    assessed_at = review.created_at
    record = AcademyCompetencyVerification(
        member_id=enrollment.member_id,
        member_auth_id=str(enrollment.member_auth_id),
        source_enrollment_id=enrollment.id,
        source_progress_id=progress.id,
        source_review_event_id=review.id,
        milestone_definition_id=definition.id,
        milestone_version_id=version.id,
        rubric_snapshot=match["rubric_snapshot"],
        criteria_passed=payload.criteria_passed,
        assessment_context_snapshot={
            **match.get("assessment_context_snapshot", {}),
            "legacy_milestone_id": str(progress.milestone_id),
            "equivalence_reason": payload.equivalence_reason.strip(),
            "source_rubric_snapshot": review.coach_notes_snapshot,
        },
        evidence_media_id=progress.evidence_media_id,
        evidence_owner_auth_id=uploader,
        verified_by_auth_id=str(current_user.user_id),
        review_notes=payload.equivalence_reason.strip(),
        verified_at=assessed_at,
        expires_at=assessed_at + timedelta(days=valid_days) if valid_days else None,
    )
    db.add(record)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Verification already exists"
        ) from exc
    await db.refresh(record)
    return _view_verification(record, "verified")


@router.post("/admin/{verification_id}/revoke", status_code=201)
async def revoke_verification(
    verification_id: uuid.UUID,
    payload: RevokeVerificationInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Human administrator required")
    record = (
        await db.execute(
            select(AcademyCompetencyVerification)
            .where(AcademyCompetencyVerification.id == verification_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Verification not found")
    existing = await db.scalar(
        select(AcademyCompetencyRevocation.id).where(
            AcademyCompetencyRevocation.verification_id == verification_id
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already revoked")
    revocation = AcademyCompetencyRevocation(
        verification_id=record.id,
        revoked_by_auth_id=str(current_user.user_id),
        reason=payload.reason.strip(),
    )
    db.add(revocation)
    await db.commit()
    return {
        "verification_id": str(record.id),
        "revocation_id": str(revocation.id),
        "status": "revoked",
    }


async def _passport(db: AsyncSession, auth_id: str) -> dict:
    records = (
        (
            await db.execute(
                select(AcademyCompetencyVerification)
                .where(AcademyCompetencyVerification.member_auth_id == auth_id)
                .order_by(
                    AcademyCompetencyVerification.verified_at.desc(),
                    AcademyCompetencyVerification.id.desc(),
                )
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    if not records:
        return {"member_auth_id": auth_id, "competencies": [], "verifications": []}
    definition_ids = {row.milestone_definition_id for row in records}
    definitions = (
        (
            await db.execute(
                select(AcademyMilestoneDefinition).where(
                    AcademyMilestoneDefinition.id.in_(definition_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    definition_by_id = {item.id: item for item in definitions}
    ids = [row.id for row in records]
    progress_ids = [row.source_progress_id for row in records]
    revocations = (
        (
            await db.execute(
                select(AcademyCompetencyRevocation).where(
                    AcademyCompetencyRevocation.verification_id.in_(ids)
                )
            )
        )
        .scalars()
        .all()
    )
    revoked_by_id = {r.verification_id: r for r in revocations}
    source_records = (
        (
            await db.execute(
                select(StudentProgress).where(StudentProgress.id.in_(progress_ids))
            )
        )
        .scalars()
        .all()
    )
    progress_by_id = {p.id: p for p in source_records}
    latest_decisions = {}
    for progress_id in {row.source_progress_id for row in records}:
        latest_decisions[progress_id] = await _latest_decision(db, progress_id)

    now = datetime.now(timezone.utc)
    views = []
    for row in records:
        progress = progress_by_id.get(row.source_progress_id)
        decision = latest_decisions[row.source_progress_id]
        revoked = revoked_by_id.get(row.id)
        if revoked:
            state, reason = "revoked", revoked.reason
        elif progress is None or decision is None:
            state, reason = "recheck_required", "Original assessment is unavailable"
        elif (
            progress.status != ProgressStatus.ACHIEVED
            or decision.new_status != ProgressStatus.ACHIEVED
            or decision.id != row.source_review_event_id
        ):
            state, reason = "recheck_required", "Original assessment was changed"
        elif progress.evidence_media_id != row.evidence_media_id:
            state, reason = "recheck_required", "Original evidence changed"
        elif row.expires_at is not None and row.expires_at <= now:
            state, reason = "expired", "Competency assessment needs renewal"
        else:
            state, reason = "verified", None
        view = _view_verification(row, state, reason=reason)
        definition = definition_by_id.get(row.milestone_definition_id)
        view["code"] = definition.code if definition else None
        view["title"] = definition.title if definition else "Unmapped competency"
        views.append(view)

    by_definition: dict[str, dict] = {}
    for view in views:
        key = view["milestone_definition_id"]
        current = by_definition.setdefault(
            key,
            {
                "milestone_definition_id": key,
                "code": view["code"],
                "title": view["title"],
                "status": "recheck_required",
                "supporting_verification_ids": [],
            },
        )
        if view["status"] == "verified":
            current["status"] = "verified"
            current["supporting_verification_ids"].append(view["id"])
        elif current["status"] != "verified" and view["status"] == "expired":
            current["status"] = "expired"
    return {
        "member_auth_id": auth_id,
        "competencies": list(by_definition.values()),
        "verifications": views,
    }


@router.get("/me")
async def my_competency_passport(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Member identity required")
    return await _passport(db, str(current_user.user_id))


@router.get("/admin/members/{member_auth_id}")
async def member_competency_passport(
    member_auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _passport(db, member_auth_id)
