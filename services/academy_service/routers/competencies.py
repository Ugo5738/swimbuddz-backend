"""Portable Academy competency passport: verified legacy evidence, not automatic credit.

This sidecar deliberately does NOT alter legacy StudentProgress, certificates,
Club eligibility, rewards or enrollment commercial terms.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client.core import internal_get
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    Enrollment,
    Milestone,
    MilestoneEventType,
    MilestoneReviewEvent,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.services.requirements_resolver import (
    requirements_sha256,
)

router = APIRouter(prefix="/competencies", tags=["academy-competency-passport"])

DECISION_EVENT_TYPES = (
    MilestoneEventType.APPROVED,
    MilestoneEventType.REJECTED,
    MilestoneEventType.OVERRIDE,
    MilestoneEventType.STATUS_CHANGED,
)

STATUS_PRIORITY = {
    "revoked": 0,
    "recheck_required": 1,
    "expired": 2,
    "verified": 3,
}


class PromoteLegacyReviewInput(BaseModel):
    source_progress_id: uuid.UUID
    milestone_version_id: uuid.UUID
    criteria_passed: list[str] = Field(min_length=1)
    equivalence_reason: str = Field(min_length=30, max_length=4000)


class RevokeVerificationInput(BaseModel):
    reason: str = Field(min_length=20, max_length=4000)


def _view_verification(
    record: AcademyCompetencyVerification,
    state: str,
    *,
    reason: str | None = None,
    revocation: AcademyCompetencyRevocation | None = None,
) -> dict:
    return {
        "id": str(record.id),
        "member_id": str(record.member_id),
        "member_auth_id": record.member_auth_id,
        "milestone_definition_id": str(record.milestone_definition_id),
        "milestone_version_id": str(record.milestone_version_id),
        "source_enrollment_id": str(record.source_enrollment_id),
        "source_progress_id": str(record.source_progress_id),
        "source_review_event_id": str(record.source_review_event_id),
        "evidence_media_id": str(record.evidence_media_id),
        "verified_by_auth_id": record.verified_by_auth_id,
        "review_notes": record.review_notes,
        "verified_at": record.verified_at,
        "expires_at": record.expires_at,
        "status": state,
        "status_reason": reason,
        "revoked_at": revocation.revoked_at if revocation else None,
        "revoked_by_auth_id": revocation.revoked_by_auth_id if revocation else None,
        "criteria_passed": record.criteria_passed,
        "assessment_context_snapshot": record.assessment_context_snapshot,
    }


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


async def _latest_decisions_batch(
    db: AsyncSession, progress_ids: set[uuid.UUID]
) -> dict[uuid.UUID, MilestoneReviewEvent]:
    """Fetch the latest review decision per progress_id in one Postgres query."""
    if not progress_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(MilestoneReviewEvent)
                .where(
                    MilestoneReviewEvent.progress_id.in_(progress_ids),
                    MilestoneReviewEvent.event_type.in_(DECISION_EVENT_TYPES),
                )
                .distinct(MilestoneReviewEvent.progress_id)
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
    return {row.progress_id: row for row in rows}


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


async def _project_verifications(
    db: AsyncSession, records: list[AcademyCompetencyVerification]
) -> list[dict]:
    """Compute live fail-closed passport states in batched queries (zero N+1)."""
    if not records:
        return []
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
    progress_ids = {row.source_progress_id for row in records}
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
    latest_decisions = await _latest_decisions_batch(db, progress_ids)

    now = datetime.now(timezone.utc)
    views = []
    for row in records:
        progress = progress_by_id.get(row.source_progress_id)
        decision = latest_decisions.get(row.source_progress_id)
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

        view = _view_verification(row, state, reason=reason, revocation=revoked)
        definition = definition_by_id.get(row.milestone_definition_id)
        view["code"] = definition.code if definition else None
        view["title"] = definition.title if definition else "Unmapped competency"
        views.append(view)
    return views


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
        v.source_review_event_id: v for v in existing_verifications
    }
    verification_by_progress = {v.source_progress_id: v for v in existing_verifications}

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

    frozen_context = match["assessment_context_snapshot"]
    if not isinstance(frozen_context, dict):
        raise HTTPException(status_code=409, detail="Invalid frozen assessment context")
    valid_days = frozen_context.get("validity_days")
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
