"""Portable Academy competency passport: legacy promotions & native V2 assessments.

This sidecar deliberately does NOT alter legacy StudentProgress, certificates,
Club eligibility, rewards or enrollment commercial terms.
"""

import uuid
from datetime import timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    is_admin_or_service,
    require_admin,
    require_coach_for_cohort,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_id
from libs.common.service_client.core import internal_get
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyDrill,
    AcademyDrillVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneRemedialDrill,
    AcademyMilestoneVersion,
    AcademyStudentDrillAssignment,
    Enrollment,
    Milestone,
    MilestoneEventType,
    MilestoneReviewEvent,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.routers.drill_assignments import (
    serialize_drill_assignments,
)
from services.academy_service.services.parity import (
    DECISION_EVENT_TYPES,
    STATUS_PRIORITY,
    compute_club_readiness_advisory,
    compute_cohort_graduation_parity,
    latest_decisions_batch as _latest_decisions_batch,
    project_verifications as _project_verifications,
    view_verification as _view_verification,
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


class CohortV2AssessmentInput(BaseModel):
    enrollment_id: uuid.UUID
    milestone_version_id: uuid.UUID
    evidence_media_id: uuid.UUID
    evidence_uploaded_by: Literal["assessor", "member"] = "assessor"
    criteria_passed: list[str] = Field(min_length=1)
    assessment_notes: str = Field(min_length=20, max_length=4000)


class DiagnosticAssessmentInput(BaseModel):
    member_id: uuid.UUID
    member_auth_id: str | None = Field(default=None, max_length=160)
    milestone_version_id: uuid.UUID
    evidence_media_id: uuid.UUID
    evidence_uploaded_by: Literal["assessor", "member"] = "assessor"
    criteria_passed: list[str] = Field(min_length=1)
    assessment_notes: str = Field(min_length=20, max_length=4000)
    clinic_label: str | None = Field(default=None, max_length=200)


class RevokeVerificationInput(BaseModel):
    reason: str = Field(min_length=20, max_length=4000)


def _human_assessor_role(user: AuthUser) -> str:
    if user.role == "service_role":
        raise HTTPException(
            status_code=403, detail="Human coach or administrator approval is required"
        )
    if is_admin_or_service(user):
        return "admin"
    if user.has_role("coach"):
        return "coach"
    raise HTTPException(
        status_code=403, detail="Coach or administrator privileges required"
    )


def _validate_rubric_and_validity(
    *,
    version: AcademyMilestoneVersion,
    rubric_snapshot: dict,
    context_snapshot: dict,
    safety_gate: bool,
    criteria_passed: list[str],
) -> int | None:
    if version.evidence_policy not in ("video", "time_trial"):
        raise HTTPException(
            status_code=409,
            detail="Portable practical credit requires video-based evidence",
        )
    if not isinstance(context_snapshot, dict):
        raise HTTPException(status_code=409, detail="Invalid assessment context")
    valid_days = context_snapshot.get("validity_days")
    if valid_days is not None and (
        isinstance(valid_days, bool)
        or not isinstance(valid_days, int)
        or valid_days <= 0
    ):
        raise HTTPException(
            status_code=409, detail="Invalid assessment validity policy"
        )
    if safety_gate and valid_days is None:
        raise HTTPException(
            status_code=409, detail="Safety-sensitive credit needs a recheck interval"
        )
    rubric_criteria = (
        rubric_snapshot.get("criteria") if isinstance(rubric_snapshot, dict) else None
    )
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
        or len(set(criteria_passed)) != len(criteria_passed)
        or set(criteria_passed) != set(expected)
    ):
        raise HTTPException(
            status_code=422,
            detail="Every published criterion must be explicitly attested",
        )
    return valid_days


async def _latest_decision(db: AsyncSession, progress_id: uuid.UUID):
    return (
        await db.execute(
            select(MilestoneReviewEvent)
            .where(
                MilestoneReviewEvent.progress_id == progress_id,
                MilestoneReviewEvent.event_type.in_(DECISION_EVENT_TYPES),
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
        return str(last_claim.actor_id)
    if last_claim.actor_role != "student" or not enrollment.member_auth_id:
        raise HTTPException(status_code=409, detail="Evidence owner cannot be verified")
    return str(enrollment.member_auth_id)


async def _check_media_evidence(
    *,
    media_id: uuid.UUID,
    owner_auth_id: str,
    expected_linked_id: str,
) -> None:
    try:
        response = await internal_get(
            service_url=get_settings().MEDIA_SERVICE_URL,
            path=f"/internal/media/alumni-evidence/{media_id}",
            calling_service="academy",
            params={
                "owner_auth_id": owner_auth_id,
                "expected_linked_id": expected_linked_id,
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


async def _check_media(progress: StudentProgress, owner_auth_id: str):
    await _check_media_evidence(
        media_id=progress.evidence_media_id,
        owner_auth_id=owner_auth_id,
        expected_linked_id=f"{progress.enrollment_id}:{progress.milestone_id}",
    )


@router.get("/admin/legacy-candidates")
async def list_legacy_promotion_candidates(
    cohort_id: uuid.UUID | None = Query(default=None),
    member_auth_id: str | None = Query(default=None),
    include_credited: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List achieved legacy StudentProgress records with V2 promotion readiness."""
    stmt = (
        select(StudentProgress, Enrollment, Milestone)
        .join(Enrollment, Enrollment.id == StudentProgress.enrollment_id)
        .join(Milestone, Milestone.id == StudentProgress.milestone_id)
        .where(StudentProgress.status == ProgressStatus.ACHIEVED)
        .order_by(
            StudentProgress.reviewed_at.desc().nullslast(),
            StudentProgress.id.desc(),
        )
        .limit(limit)
    )
    if cohort_id is not None:
        stmt = stmt.where(Enrollment.cohort_id == cohort_id)
    if member_auth_id is not None:
        stmt = stmt.where(Enrollment.member_auth_id == member_auth_id)

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    progress_ids = {progress.id for progress, _, _ in rows}
    cohort_ids = {
        enrollment.cohort_id for _, enrollment, _ in rows if enrollment.cohort_id
    }

    latest_decisions = await _latest_decisions_batch(db, progress_ids)

    claim_rows = (
        (
            await db.execute(
                select(MilestoneReviewEvent)
                .where(
                    MilestoneReviewEvent.progress_id.in_(progress_ids),
                    MilestoneReviewEvent.event_type == MilestoneEventType.CLAIMED,
                )
                .order_by(
                    MilestoneReviewEvent.progress_id,
                    MilestoneReviewEvent.created_at.desc(),
                    MilestoneReviewEvent.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    claims_by_progress: dict[uuid.UUID, list[MilestoneReviewEvent]] = {}
    for event in claim_rows:
        claims_by_progress.setdefault(event.progress_id, []).append(event)

    frozen_cohort_ids = (
        set(
            (
                await db.execute(
                    select(AcademyCohortRequirementsSnapshot.cohort_id).where(
                        AcademyCohortRequirementsSnapshot.cohort_id.in_(cohort_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if cohort_ids
        else set()
    )

    existing_verifications = (
        (
            await db.execute(
                select(AcademyCompetencyVerification).where(
                    AcademyCompetencyVerification.source_progress_id.in_(progress_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    verification_by_review = {
        v.source_review_event_id: v
        for v in existing_verifications
        if v.source_review_event_id is not None
    }
    verification_by_progress = {
        v.source_progress_id: v
        for v in existing_verifications
        if v.source_progress_id is not None
    }

    items = []
    for progress, enrollment, legacy_milestone in rows:
        decision = latest_decisions.get(progress.id)
        existing = (
            verification_by_review.get(decision.id)
            if decision is not None
            else verification_by_progress.get(progress.id)
        )
        already_credited = existing is not None
        if already_credited and not include_credited:
            continue

        blockers: list[str] = []
        if enrollment.cohort_id is None or not enrollment.member_auth_id:
            blockers.append("Missing verifiable cohort or member identity")
        has_frozen_snapshot = (
            enrollment.cohort_id is not None
            and enrollment.cohort_id in frozen_cohort_ids
        )
        if not has_frozen_snapshot:
            blockers.append("Cohort does not have frozen V2 requirements")

        has_human_approved = (
            progress.reviewed_at is not None
            and decision is not None
            and decision.new_status == ProgressStatus.ACHIEVED
            and decision.event_type
            in (MilestoneEventType.APPROVED, MilestoneEventType.OVERRIDE)
            and decision.actor_role in ("coach", "admin")
        )
        if not has_human_approved:
            blockers.append("Missing human-approved source review")

        if progress.evidence_media_id is None:
            blockers.append("No video evidence attached")
        elif (
            decision is not None
            and decision.evidence_media_id_snapshot != progress.evidence_media_id
        ):
            blockers.append("Source evidence differs from reviewed snapshot")

        matching_claim = next(
            (
                claim
                for claim in claims_by_progress.get(progress.id, [])
                if progress.evidence_media_id is not None
                and claim.evidence_media_id_snapshot == progress.evidence_media_id
                and claim.actor_role in ("student", "coach")
            ),
            None,
        )
        has_claim_provenance = matching_claim is not None
        if progress.evidence_media_id is not None and not has_claim_provenance:
            blockers.append("Missing original CLAIMED evidence provenance event")

        items.append(
            {
                "source_progress_id": str(progress.id),
                "enrollment_id": str(enrollment.id),
                "cohort_id": str(enrollment.cohort_id)
                if enrollment.cohort_id
                else None,
                "program_id": str(enrollment.program_id),
                "member_id": str(enrollment.member_id),
                "member_auth_id": enrollment.member_auth_id,
                "legacy_milestone_id": str(legacy_milestone.id),
                "legacy_milestone_name": legacy_milestone.name,
                "achieved_at": progress.achieved_at,
                "reviewed_at": progress.reviewed_at,
                "evidence_media_id": (
                    str(progress.evidence_media_id)
                    if progress.evidence_media_id
                    else None
                ),
                "coach_notes": progress.coach_notes,
                "has_frozen_snapshot": has_frozen_snapshot,
                "has_human_approved_review": has_human_approved,
                "has_claim_provenance": has_claim_provenance,
                "already_credited": already_credited,
                "existing_verification_id": str(existing.id) if existing else None,
                "eligible": not blockers and not already_credited,
                "eligibility_blockers": blockers,
            }
        )
    return items


@router.get("/admin/verifications")
async def list_admin_verifications(
    cohort_id: uuid.UUID | None = Query(default=None),
    member_auth_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List issued competency verifications with live status for admin review."""
    stmt = (
        select(AcademyCompetencyVerification)
        .order_by(
            AcademyCompetencyVerification.verified_at.desc(),
            AcademyCompetencyVerification.id.desc(),
        )
        .limit(limit)
    )
    if member_auth_id is not None:
        stmt = stmt.where(
            AcademyCompetencyVerification.member_auth_id == member_auth_id
        )
    if cohort_id is not None:
        stmt = stmt.join(
            Enrollment,
            Enrollment.id == AcademyCompetencyVerification.source_enrollment_id,
        ).where(Enrollment.cohort_id == cohort_id)

    records = (await db.execute(stmt)).scalars().all()
    return await _project_verifications(db, records)


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
    if len(payload.equivalence_reason.strip()) < 30:
        raise HTTPException(
            status_code=422, detail="A meaningful equivalence explanation is required"
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
    if (
        match is None
        or match.get("rubric_snapshot") != version.rubric_json
        or match.get("assessment_context_snapshot") != version.assessment_context_json
        or match.get("safety_gate") != bool(version.safety_gate)
    ):
        raise HTTPException(
            status_code=409,
            detail="Published assessment does not match the cohort's frozen requirements",
        )

    valid_days = _validate_rubric_and_validity(
        version=version,
        rubric_snapshot=match["rubric_snapshot"],
        context_snapshot=match["assessment_context_snapshot"],
        safety_gate=bool(version.safety_gate),
        criteria_passed=payload.criteria_passed,
    )

    already = (
        await db.execute(
            select(AcademyCompetencyVerification).where(
                AcademyCompetencyVerification.source_review_event_id == review.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        raise HTTPException(status_code=409, detail="Source review already credited")

    uploader = await _uploader_auth_id(db, progress, enrollment)
    await _check_media(progress, uploader)
    current_review = await _source_review(db, progress)
    if current_review.id != review.id:
        raise HTTPException(
            status_code=409, detail="Source review changed during verification"
        )

    assessed_at = review.created_at
    record = AcademyCompetencyVerification(
        member_id=enrollment.member_id,
        member_auth_id=str(enrollment.member_auth_id),
        source_type="legacy_promotion",
        assessor_role="admin",
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
            "legacy_review_notes_snapshot": review.coach_notes_snapshot,
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


@router.post("/coach/assess-cohort", status_code=201)
async def assess_cohort_v2_competency(
    payload: CohortV2AssessmentInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Assigned cohort coach or human admin issues a native V2 cohort assessment."""
    assessor_role = _human_assessor_role(current_user)
    if len(payload.assessment_notes.strip()) < 20:
        raise HTTPException(
            status_code=422, detail="Substantive assessment notes are required"
        )

    enrollment = (
        await db.execute(
            select(Enrollment)
            .where(Enrollment.id == payload.enrollment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    if enrollment.cohort_id is None or not enrollment.member_auth_id:
        raise HTTPException(
            status_code=409, detail="Verifiable cohort enrollment is required"
        )
    await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)

    version = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    if version is None or version.state != "published" or version.published_at is None:
        raise HTTPException(
            status_code=409, detail="Published milestone version required"
        )
    definition = await db.get(AcademyMilestoneDefinition, version.definition_id)
    if definition is None:
        raise HTTPException(status_code=409, detail="Missing canonical definition")

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
    if (
        match is None
        or match.get("rubric_snapshot") != version.rubric_json
        or match.get("assessment_context_snapshot") != version.assessment_context_json
        or match.get("safety_gate") != bool(version.safety_gate)
    ):
        raise HTTPException(
            status_code=409,
            detail="Published assessment does not match the cohort's frozen requirements",
        )

    valid_days = _validate_rubric_and_validity(
        version=version,
        rubric_snapshot=match["rubric_snapshot"],
        context_snapshot=match["assessment_context_snapshot"],
        safety_gate=bool(version.safety_gate),
        criteria_passed=payload.criteria_passed,
    )

    owner_auth_id = (
        str(current_user.user_id)
        if payload.evidence_uploaded_by == "assessor"
        else str(enrollment.member_auth_id)
    )
    await _check_media_evidence(
        media_id=payload.evidence_media_id,
        owner_auth_id=owner_auth_id,
        expected_linked_id=f"{enrollment.id}:{version.id}",
    )

    now = utc_now()
    record = AcademyCompetencyVerification(
        member_id=enrollment.member_id,
        member_auth_id=str(enrollment.member_auth_id),
        source_type="cohort_v2",
        assessor_role=assessor_role,
        source_enrollment_id=enrollment.id,
        source_progress_id=None,
        source_review_event_id=None,
        milestone_definition_id=definition.id,
        milestone_version_id=version.id,
        rubric_snapshot=match["rubric_snapshot"],
        criteria_passed=payload.criteria_passed,
        assessment_context_snapshot={
            **match.get("assessment_context_snapshot", {}),
            "cohort_id": str(enrollment.cohort_id),
            "cohort_snapshot_sha256": snapshot.sha256,
            "evidence_uploaded_by": payload.evidence_uploaded_by,
        },
        evidence_media_id=payload.evidence_media_id,
        evidence_owner_auth_id=owner_auth_id,
        verified_by_auth_id=str(current_user.user_id),
        review_notes=payload.assessment_notes.strip(),
        verified_at=now,
        expires_at=now + timedelta(days=valid_days) if valid_days else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    view = _view_verification(record, "verified")
    view["code"] = definition.code
    view["title"] = definition.title
    return view


@router.post("/admin/assess-diagnostic", status_code=201)
async def assess_diagnostic_competency(
    payload: DiagnosticAssessmentInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Human coach or admin issues portable prerequisite credit from a diagnostic clinic."""
    assessor_role = _human_assessor_role(current_user)
    if len(payload.assessment_notes.strip()) < 20:
        raise HTTPException(
            status_code=422, detail="Substantive assessment notes are required"
        )

    member_auth_id = (payload.member_auth_id or "").strip()
    if not member_auth_id:
        member = await get_member_by_id(
            str(payload.member_id), calling_service="academy"
        )
        if not member or not member.get("auth_id"):
            raise HTTPException(
                status_code=404, detail="Member profile or auth identity not found"
            )
        member_auth_id = str(member["auth_id"])

    version = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    if version is None or version.state != "published" or version.published_at is None:
        raise HTTPException(
            status_code=409, detail="Published milestone version required"
        )
    definition = await db.get(AcademyMilestoneDefinition, version.definition_id)
    if definition is None or definition.is_retired:
        raise HTTPException(
            status_code=409, detail="Active canonical milestone definition required"
        )

    valid_days = _validate_rubric_and_validity(
        version=version,
        rubric_snapshot=version.rubric_json,
        context_snapshot=version.assessment_context_json,
        safety_gate=bool(version.safety_gate),
        criteria_passed=payload.criteria_passed,
    )

    owner_auth_id = (
        str(current_user.user_id)
        if payload.evidence_uploaded_by == "assessor"
        else member_auth_id
    )
    await _check_media_evidence(
        media_id=payload.evidence_media_id,
        owner_auth_id=owner_auth_id,
        expected_linked_id=f"diagnostic:{payload.member_id}:{version.id}",
    )

    now = utc_now()
    context = dict(version.assessment_context_json or {})
    context["evidence_uploaded_by"] = payload.evidence_uploaded_by
    if payload.clinic_label and payload.clinic_label.strip():
        context["clinic_label"] = payload.clinic_label.strip()

    record = AcademyCompetencyVerification(
        member_id=payload.member_id,
        member_auth_id=member_auth_id,
        source_type="diagnostic_assessment",
        assessor_role=assessor_role,
        source_enrollment_id=None,
        source_progress_id=None,
        source_review_event_id=None,
        milestone_definition_id=definition.id,
        milestone_version_id=version.id,
        rubric_snapshot=version.rubric_json,
        criteria_passed=payload.criteria_passed,
        assessment_context_snapshot=context,
        evidence_media_id=payload.evidence_media_id,
        evidence_owner_auth_id=owner_auth_id,
        verified_by_auth_id=str(current_user.user_id),
        review_notes=payload.assessment_notes.strip(),
        verified_at=now,
        expires_at=now + timedelta(days=valid_days) if valid_days else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    view = _view_verification(record, "verified")
    view["code"] = definition.code
    view["title"] = definition.title
    return view


@router.post("/admin/{verification_id}/revoke", status_code=201)
async def revoke_verification(
    verification_id: uuid.UUID,
    payload: RevokeVerificationInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Human administrator required")
    if len(payload.reason.strip()) < 20:
        raise HTTPException(
            status_code=422, detail="A meaningful revocation reason is required"
        )
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

    views = await _project_verifications(db, records)

    by_definition: dict[str, dict] = {}
    for view in views:
        key = view["milestone_definition_id"]
        status = view["status"]
        current = by_definition.get(key)
        if current is None:
            current = {
                "milestone_definition_id": key,
                "code": view["code"],
                "title": view["title"],
                "status": status,
                "supporting_verification_ids": [],
            }
            by_definition[key] = current
        elif STATUS_PRIORITY.get(status, 0) > STATUS_PRIORITY.get(current["status"], 0):
            current["status"] = status
        if status == "verified":
            current["supporting_verification_ids"].append(view["id"])
    return {
        "member_auth_id": auth_id,
        "competencies": list(by_definition.values()),
        "verifications": views,
    }


@router.get("/enrollments/{enrollment_id}/v2-plan")
async def get_enrollment_v2_learning_plan(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Read-only V2 learning plan: frozen cohort milestones + prior credit + remedial drills."""
    enrollment = await db.get(Enrollment, enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    is_owner = current_user.role != "service_role" and enrollment.member_auth_id == str(
        current_user.user_id
    )
    if not is_owner:
        if enrollment.cohort_id:
            await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
        elif not (
            current_user.role != "service_role" and is_admin_or_service(current_user)
        ):
            raise HTTPException(status_code=403, detail="Not authorized")

    snapshot = (
        await db.scalar(
            select(AcademyCohortRequirementsSnapshot).where(
                AcademyCohortRequirementsSnapshot.cohort_id == enrollment.cohort_id
            )
        )
        if enrollment.cohort_id
        else None
    )

    passport = (
        await _passport(db, str(enrollment.member_auth_id))
        if enrollment.member_auth_id
        else {"competencies": [], "verifications": []}
    )
    summary_by_def = {c["milestone_definition_id"]: c for c in passport["competencies"]}
    verifications_by_def: dict[str, list[dict]] = {}
    for v in passport["verifications"]:
        verifications_by_def.setdefault(v["milestone_definition_id"], []).append(v)

    # Load drill assignments for this enrollment (plus standalone member diagnostic drills)
    drill_filters = [AcademyStudentDrillAssignment.enrollment_id == enrollment_id]
    if enrollment.member_auth_id:
        drill_filters.append(
            (AcademyStudentDrillAssignment.member_auth_id == enrollment.member_auth_id)
            & (AcademyStudentDrillAssignment.enrollment_id.is_(None))
        )
    assigned_rows = (
        (
            await db.execute(
                select(AcademyStudentDrillAssignment)
                .where(
                    or_(*drill_filters),
                    AcademyStudentDrillAssignment.status != "archived",
                )
                .order_by(AcademyStudentDrillAssignment.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    drill_assignments = await serialize_drill_assignments(db, assigned_rows)

    if snapshot is None:
        return {
            "enrollment_id": str(enrollment.id),
            "cohort_id": str(enrollment.cohort_id) if enrollment.cohort_id else None,
            "has_frozen_requirements": False,
            "snapshot_sha256": None,
            "milestones": [],
            "drill_assignments": drill_assignments,
        }

    raw_milestones = snapshot.requirements_json.get("milestones", [])
    def_uuids = {
        uuid.UUID(m["definition_id"]) for m in raw_milestones if m.get("definition_id")
    }

    # Batch load remedial drills + latest published drill versions for every milestone
    remedial_by_def: dict[str, list[dict]] = {}
    if def_uuids:
        remedial_links = (
            (
                await db.execute(
                    select(AcademyMilestoneRemedialDrill)
                    .where(
                        AcademyMilestoneRemedialDrill.milestone_definition_id.in_(
                            def_uuids
                        )
                    )
                    .order_by(
                        AcademyMilestoneRemedialDrill.milestone_definition_id,
                        AcademyMilestoneRemedialDrill.priority_order,
                        AcademyMilestoneRemedialDrill.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if remedial_links:
            drill_ids = {link.drill_id for link in remedial_links}
            drills = (
                (
                    await db.execute(
                        select(AcademyDrill).where(
                            AcademyDrill.id.in_(drill_ids),
                            AcademyDrill.is_retired.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
            drill_by_id = {d.id: d for d in drills}
            published_versions = (
                (
                    await db.execute(
                        select(AcademyDrillVersion)
                        .where(
                            AcademyDrillVersion.drill_id.in_(drill_ids),
                            AcademyDrillVersion.state == "published",
                        )
                        .distinct(AcademyDrillVersion.drill_id)
                        .order_by(
                            AcademyDrillVersion.drill_id,
                            AcademyDrillVersion.version.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            ver_by_drill = {v.drill_id: v for v in published_versions}
            for link in remedial_links:
                drill = drill_by_id.get(link.drill_id)
                ver = ver_by_drill.get(link.drill_id)
                if drill is None or ver is None:
                    continue
                remedial_by_def.setdefault(
                    str(link.milestone_definition_id), []
                ).append(
                    {
                        "drill_id": str(drill.id),
                        "drill_version_id": str(ver.id),
                        "code": drill.code,
                        "title": drill.title,
                        "version": ver.version,
                        "primary_focal_cue": ver.primary_focal_cue,
                        "steps_json": ver.steps_json,
                        "requires_supervision": bool(ver.requires_supervision),
                        "coach_tip": link.coach_tip,
                        "priority_order": link.priority_order,
                    }
                )

    projected_milestones = []
    for item in raw_milestones:
        def_id = item.get("definition_id")
        summary = summary_by_def.get(def_id) if def_id else None
        status = summary["status"] if summary else "unverified"
        def_verifications = verifications_by_def.get(def_id, []) if def_id else []
        active_verified = [v for v in def_verifications if v["status"] == "verified"]
        primary_verification = active_verified[0] if active_verified else None
        prior_credit = bool(
            primary_verification is not None
            and (
                primary_verification["source_type"] == "diagnostic_assessment"
                or primary_verification["source_enrollment_id"] != str(enrollment.id)
            )
        )
        projected_milestones.append(
            {
                **item,
                "passport_status": status,
                "prior_credit": prior_credit,
                "supporting_verification_id": (
                    primary_verification["id"] if primary_verification else None
                ),
                "verified_source_type": (
                    primary_verification["source_type"]
                    if primary_verification
                    else None
                ),
                "verified_at": (
                    primary_verification["verified_at"]
                    if primary_verification
                    else None
                ),
                "expires_at": (
                    primary_verification["expires_at"] if primary_verification else None
                ),
                "remedial_drills": (
                    [] if status == "verified" else remedial_by_def.get(def_id, [])
                ),
            }
        )

    return {
        "enrollment_id": str(enrollment.id),
        "cohort_id": str(enrollment.cohort_id) if enrollment.cohort_id else None,
        "has_frozen_requirements": True,
        "snapshot_sha256": snapshot.sha256,
        "milestones": projected_milestones,
        "drill_assignments": drill_assignments,
    }


@router.get("/me")
async def my_competency_passport(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    if current_user.role == "service_role":
        raise HTTPException(status_code=403, detail="Member identity required")
    return await _passport(db, str(current_user.user_id))


@router.get("/admin/cohorts/{cohort_id}/graduation-parity")
async def get_cohort_graduation_parity(
    cohort_id: uuid.UUID,
    include_inactive: bool = Query(default=False),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Read-only Phase 5A dual-read shadow comparison: legacy vs. V2 graduation eligibility."""
    return await compute_cohort_graduation_parity(
        db, cohort_id, include_inactive=include_inactive
    )


@router.get("/admin/members/{member_auth_id}/club-readiness-advisory")
async def member_club_readiness_advisory(
    member_auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Read-only Phase 5A advisory Club-readiness summary from V2 verified credentials."""
    return await compute_club_readiness_advisory(db, member_auth_id)


@router.get("/admin/members/{member_auth_id}")
async def member_competency_passport(
    member_auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await _passport(db, member_auth_id)
