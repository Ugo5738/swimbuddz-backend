"""Read-only Phase 5A dual-read graduation parity and Club-readiness advisory engine.

All functions in this module are strictly read-only projections:
- They NEVER mutate Enrollment.status, certificate_issued_at, certificate_code,
  StudentProgress, or ClubApplication.
- They NEVER auto-issue certificates or auto-approve Club admission (ADR-005, ADR-010).
- All database reads are batched (zero N+1) and non-PII structured logging is used.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_members_bulk
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    Cohort,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    MilestoneEventType,
    MilestoneReviewEvent,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.models.quizzes import (
    AcademyQuizAssignment,
    AcademyQuizAttempt,
)
from services.academy_service.services.requirements_resolver import (
    requirements_sha256,
)

logger = get_logger(__name__)

GraduationMode = Literal["legacy", "shadow_log", "v2_enforced"]
_VALID_GRADUATION_MODES: set[str] = {"legacy", "shadow_log", "v2_enforced"}


def get_graduation_mode() -> GraduationMode:
    """Return normalized Academy V2 graduation consumer mode (fails safe to 'legacy')."""
    raw = getattr(get_settings(), "ACADEMY_V2_GRADUATION_MODE", "legacy") or "legacy"
    normalized = raw.strip().lower()
    if normalized not in _VALID_GRADUATION_MODES:
        logger.warning(
            "Unknown ACADEMY_V2_GRADUATION_MODE=%r; failing safe to 'legacy'",
            raw,
        )
        return "legacy"
    return normalized  # type: ignore[return-value]


def get_graduation_mode_summary() -> dict[str, Any]:
    """Read-only status payload surfacing the active Phase 5B graduation consumer mode."""
    mode = get_graduation_mode()
    descriptions = {
        "legacy": (
            "Legacy milestone completion governs certificate issuance and PDF downloads "
            "(default safe mode)."
        ),
        "shadow_log": (
            "Legacy milestone completion still governs certificates while the daily job "
            "records non-PII V2 dual-read parity telemetry."
        ),
        "v2_enforced": (
            "Frozen cohorts require live verified V2 competency credentials (including "
            "safety gates) and required cohort quizzes for certificate issuance and PDF "
            "downloads; unfrozen historical cohorts safely retain legacy rules."
        ),
    }
    return {
        "mode": mode,
        "v2_enforced_active": mode == "v2_enforced",
        "shadow_logging_active": mode == "shadow_log",
        "legacy_fallback_for_unfrozen_cohorts": True,
        "description": descriptions[mode],
    }


DECISION_EVENT_TYPES = (
    MilestoneEventType.APPROVED,
    MilestoneEventType.REJECTED,
    MilestoneEventType.OVERRIDE,
    MilestoneEventType.STATUS_CHANGED,
)

STATUS_PRIORITY: dict[str, int] = {
    "revoked": 0,
    "recheck_required": 1,
    "expired": 2,
    "verified": 3,
}

# Advisory mapping from canonical Academy V2 safety-gate codes to Club readiness checks.
# Never inferred by fuzzy name matching; strictly explicit canonical codes (ADR-010).
CLUB_CORE_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "check_key": "continuous_25m",
        "milestone_code": "MS-S1-FREE-25M",
        "label": "25m continuous freestyle with side breathing",
        "required_for_club_profile": True,
    },
    {
        "check_key": "deep_water_tread",
        "milestone_code": "MS-A1-TREAD-60S",
        "label": "60s deep-water tread & self-rescue recovery",
        "required_for_club_profile": True,
    },
    {
        "check_key": "self_rescue_float",
        "milestone_code": "MS-F3-BACK-FLOAT-30S",
        "label": "30s unassisted back float & calm stand",
        "required_for_club_profile": False,
    },
    {
        "check_key": "safe_entry_recovery",
        "milestone_code": "MS-F1-SAFE-ENTRY",
        "label": "Safe pool entry, orientation & calm exit",
        "required_for_club_profile": False,
    },
)

CLUB_ADVISORY_NOTE = (
    "Advisory evidence from the Academy V2 Competency Passport only. "
    "Human reviewer approval and first-session live verification rules remain "
    "mandatory per ADR-005."
)


def view_verification(
    record: AcademyCompetencyVerification,
    state: str,
    *,
    reason: str | None = None,
    revocation: AcademyCompetencyRevocation | None = None,
) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "member_id": str(record.member_id),
        "member_auth_id": record.member_auth_id,
        "source_type": record.source_type,
        "assessor_role": record.assessor_role,
        "milestone_definition_id": str(record.milestone_definition_id),
        "milestone_version_id": str(record.milestone_version_id),
        "source_enrollment_id": (
            str(record.source_enrollment_id) if record.source_enrollment_id else None
        ),
        "source_progress_id": (
            str(record.source_progress_id) if record.source_progress_id else None
        ),
        "source_review_event_id": (
            str(record.source_review_event_id)
            if record.source_review_event_id
            else None
        ),
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


async def latest_decisions_batch(
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


async def project_verifications(
    db: AsyncSession,
    records: list[AcademyCompetencyVerification],
    *,
    include_safety_gate: bool = False,
) -> list[dict[str, Any]]:
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

    version_by_id: dict[uuid.UUID, AcademyMilestoneVersion] = {}
    if include_safety_gate:
        version_ids = {row.milestone_version_id for row in records}
        versions = (
            (
                await db.execute(
                    select(AcademyMilestoneVersion).where(
                        AcademyMilestoneVersion.id.in_(version_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        version_by_id = {item.id: item for item in versions}

    ids = [row.id for row in records]
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

    legacy_progress_ids = {
        row.source_progress_id
        for row in records
        if row.source_type == "legacy_promotion" and row.source_progress_id is not None
    }
    progress_by_id: dict[uuid.UUID, StudentProgress] = {}
    latest_decisions: dict[uuid.UUID, MilestoneReviewEvent] = {}
    if legacy_progress_ids:
        source_records = (
            (
                await db.execute(
                    select(StudentProgress).where(
                        StudentProgress.id.in_(legacy_progress_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        progress_by_id = {p.id: p for p in source_records}
        latest_decisions = await latest_decisions_batch(db, legacy_progress_ids)

    now = datetime.now(timezone.utc)
    views: list[dict[str, Any]] = []
    for row in records:
        revoked = revoked_by_id.get(row.id)
        if revoked:
            state, reason = "revoked", revoked.reason
        elif row.source_type == "legacy_promotion":
            progress = (
                progress_by_id.get(row.source_progress_id)
                if row.source_progress_id
                else None
            )
            decision = (
                latest_decisions.get(row.source_progress_id)
                if row.source_progress_id
                else None
            )
            if progress is None or decision is None:
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
        else:
            if row.expires_at is not None and row.expires_at <= now:
                state, reason = "expired", "Competency assessment needs renewal"
            else:
                state, reason = "verified", None

        view = view_verification(row, state, reason=reason, revocation=revoked)
        definition = definition_by_id.get(row.milestone_definition_id)
        view["code"] = definition.code if definition else None
        view["title"] = definition.title if definition else "Unmapped competency"
        if include_safety_gate:
            version = version_by_id.get(row.milestone_version_id)
            view["safety_gate"] = bool(version.safety_gate) if version else False
            view["milestone_version_number"] = version.version if version else None
        views.append(view)
    return views


async def compute_cohort_graduation_parity(
    db: AsyncSession,
    cohort_id: uuid.UUID,
    *,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Shadow-compare legacy graduation eligibility vs. V2 frozen-snapshot eligibility.

    Legacy rule matches `tasks/reporting.py:check_and_issue_certificates`:
    all legacy `Milestone` records for `cohort.program_id` achieved in `StudentProgress`.
    V2 rule fails closed: requires a SHA-256-verified frozen `AcademyCohortRequirementsSnapshot`
    with >= 1 required milestone, and every required milestone (including all `safety_gate`
    milestones) backed by a live `verified` credential in `academy_competency_verifications`.
    """
    cohort = (
        await db.execute(
            select(Cohort)
            .options(selectinload(Cohort.program))
            .where(Cohort.id == cohort_id)
        )
    ).scalar_one_or_none()
    if cohort is None:
        raise HTTPException(status_code=404, detail="Cohort not found")

    program = cohort.program
    now = utc_now()

    # 1. Legacy program milestones (single query)
    legacy_milestones = (
        (
            await db.execute(
                select(Milestone)
                .where(Milestone.program_id == cohort.program_id)
                .order_by(Milestone.order_index.asc(), Milestone.id.asc())
            )
        )
        .scalars()
        .all()
    )
    legacy_total = len(legacy_milestones)
    legacy_milestone_ids = {m.id for m in legacy_milestones}

    # 2. Cohort enrollments (single query, bounded)
    enrollment_stmt = select(Enrollment).where(Enrollment.cohort_id == cohort.id)
    if not include_inactive:
        enrollment_stmt = enrollment_stmt.where(
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.GRADUATED]
            )
        )
    enrollments = (
        (
            await db.execute(
                enrollment_stmt.order_by(
                    Enrollment.created_at.asc(), Enrollment.id.asc()
                ).limit(500)
            )
        )
        .scalars()
        .all()
    )
    enrollment_ids = [e.id for e in enrollments]

    # 3. Achieved legacy StudentProgress for all enrollments in one query
    achieved_by_enrollment: dict[uuid.UUID, set[uuid.UUID]] = {}
    if enrollment_ids and legacy_milestone_ids:
        progress_rows = (
            await db.execute(
                select(
                    StudentProgress.enrollment_id,
                    StudentProgress.milestone_id,
                ).where(
                    StudentProgress.enrollment_id.in_(enrollment_ids),
                    StudentProgress.milestone_id.in_(legacy_milestone_ids),
                    StudentProgress.status == ProgressStatus.ACHIEVED,
                )
            )
        ).all()
        for enrollment_id, milestone_id in progress_rows:
            achieved_by_enrollment.setdefault(enrollment_id, set()).add(milestone_id)

    # 4. Frozen V2 cohort requirements snapshot (single query + integrity check)
    snapshot = (
        await db.execute(
            select(AcademyCohortRequirementsSnapshot).where(
                AcademyCohortRequirementsSnapshot.cohort_id == cohort.id
            )
        )
    ).scalar_one_or_none()
    has_frozen_snapshot = snapshot is not None
    snapshot_integrity_valid = False
    required_v2_milestones: list[dict[str, Any]] = []
    safety_gate_count = 0
    if snapshot is not None:
        snapshot_integrity_valid = (
            snapshot.program_id == cohort.program_id
            and requirements_sha256(snapshot.requirements_json) == snapshot.sha256
        )
        if snapshot_integrity_valid:
            raw_milestones = snapshot.requirements_json.get("milestones", [])
            required_v2_milestones = [
                m
                for m in raw_milestones
                if bool(m.get("required_for_completion")) or bool(m.get("safety_gate"))
            ]
            safety_gate_count = sum(
                1 for m in required_v2_milestones if bool(m.get("safety_gate"))
            )

    # 5. Batched V2 competency verifications across all cohort members (zero N+1)
    auth_ids = {e.member_auth_id for e in enrollments if e.member_auth_id}
    verifs_by_member_def: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if auth_ids and has_frozen_snapshot and snapshot_integrity_valid:
        records = (
            (
                await db.execute(
                    select(AcademyCompetencyVerification)
                    .where(AcademyCompetencyVerification.member_auth_id.in_(auth_ids))
                    .order_by(
                        AcademyCompetencyVerification.verified_at.desc(),
                        AcademyCompetencyVerification.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        projected = await project_verifications(db, records)
        for view in projected:
            key = (view["member_auth_id"], view["milestone_definition_id"])
            verifs_by_member_def.setdefault(key, []).append(view)

    # 6. Best-effort bulk member name lookup (never breaks parity if Members is unavailable)
    members_map: dict[str, dict[str, Any]] = {}
    if enrollments:
        member_ids = list({str(e.member_id) for e in enrollments})
        try:
            members = await get_members_bulk(member_ids, calling_service="academy")
            members_map = {str(m["id"]): m for m in members if m.get("id")}
        except Exception:
            logger.debug(
                "Skipping optional member name hydration for parity cohort_id=%s",
                cohort.id,
            )

    # 7. Evaluate every enrollment side by side
    counts = {
        "aligned_eligible": 0,
        "aligned_ineligible": 0,
        "legacy_only_eligible": 0,
        "v2_only_eligible": 0,
        "no_frozen_snapshot": 0,
        "snapshot_integrity_error": 0,
    }
    rows: list[dict[str, Any]] = []

    for enrollment in enrollments:
        achieved_ids = achieved_by_enrollment.get(enrollment.id, set())
        legacy_achieved_count = len(achieved_ids)
        legacy_missing_names = [
            m.name for m in legacy_milestones if m.id not in achieved_ids
        ]
        legacy_eligible = legacy_total > 0 and legacy_achieved_count >= legacy_total

        v2_verified_required_count = 0
        v2_missing_required_codes: list[str] = []
        v2_missing_safety_gate_codes: list[str] = []
        v2_prior_credit_codes: list[str] = []
        v2_blocked_details: list[dict[str, Any]] = []

        if not has_frozen_snapshot:
            v2_eligible = False
            parity_status = "no_frozen_snapshot"
        elif not snapshot_integrity_valid:
            v2_eligible = False
            parity_status = "snapshot_integrity_error"
        else:
            auth_id = enrollment.member_auth_id
            for milestone in required_v2_milestones:
                def_id = str(milestone.get("definition_id") or "")
                code = str(milestone.get("code") or def_id)
                is_safety_gate = bool(milestone.get("safety_gate"))
                candidate_verifs = (
                    verifs_by_member_def.get((auth_id, def_id), []) if auth_id else []
                )
                active_verified = [
                    v for v in candidate_verifs if v["status"] == "verified"
                ]
                if active_verified:
                    v2_verified_required_count += 1
                    is_prior_credit = not any(
                        v["source_enrollment_id"] == str(enrollment.id)
                        for v in active_verified
                    )
                    if is_prior_credit:
                        v2_prior_credit_codes.append(code)
                else:
                    v2_missing_required_codes.append(code)
                    if is_safety_gate:
                        v2_missing_safety_gate_codes.append(code)
                    if candidate_verifs:
                        best = max(
                            candidate_verifs,
                            key=lambda item: STATUS_PRIORITY.get(item["status"], -1),
                        )
                        v2_blocked_details.append(
                            {
                                "code": code,
                                "title": milestone.get("title") or code,
                                "safety_gate": is_safety_gate,
                                "status": best["status"],
                                "reason": best["status_reason"],
                            }
                        )

            v2_eligible = (
                bool(auth_id)
                and len(required_v2_milestones) > 0
                and len(v2_missing_required_codes) == 0
                and len(v2_missing_safety_gate_codes) == 0
            )
            if legacy_eligible and v2_eligible:
                parity_status = "aligned_eligible"
            elif not legacy_eligible and not v2_eligible:
                parity_status = "aligned_ineligible"
            elif legacy_eligible and not v2_eligible:
                parity_status = "legacy_only_eligible"
            else:
                parity_status = "v2_only_eligible"

        counts[parity_status] += 1
        member_info = members_map.get(str(enrollment.member_id), {})
        first_name = (member_info.get("first_name") or "").strip()
        last_name = (member_info.get("last_name") or "").strip()
        member_name = f"{first_name} {last_name}".strip() or None

        rows.append(
            {
                "enrollment_id": str(enrollment.id),
                "member_id": str(enrollment.member_id),
                "member_auth_id": enrollment.member_auth_id,
                "member_name": member_name,
                "enrollment_status": enrollment.status.value,
                "certificate_issued": bool(
                    enrollment.certificate_issued_at and enrollment.certificate_code
                ),
                "certificate_issued_at": enrollment.certificate_issued_at,
                "certificate_code": enrollment.certificate_code,
                "legacy_achieved_count": legacy_achieved_count,
                "legacy_total_milestones": legacy_total,
                "legacy_eligible": legacy_eligible,
                "legacy_missing_milestones": legacy_missing_names,
                "v2_verified_required_count": v2_verified_required_count,
                "v2_required_total": len(required_v2_milestones),
                "v2_eligible": v2_eligible,
                "v2_missing_required_codes": v2_missing_required_codes,
                "v2_missing_safety_gate_codes": v2_missing_safety_gate_codes,
                "v2_prior_credit_codes": v2_prior_credit_codes,
                "v2_blocked_details": v2_blocked_details,
                "parity_status": parity_status,
            }
        )

    total_enrollments = len(rows)
    aligned_count = counts["aligned_eligible"] + counts["aligned_ineligible"]
    discrepancy_count = counts["legacy_only_eligible"] + counts["v2_only_eligible"]
    snapshot_usable = has_frozen_snapshot and snapshot_integrity_valid
    parity_rate_percent = (
        round((aligned_count / total_enrollments) * 100.0, 1)
        if total_enrollments > 0 and snapshot_usable
        else None
    )

    # Non-PII discrepancy telemetry per ACADEMY_COMPETENCY_ARCHITECTURE_V2 §5 Phase 3
    if discrepancy_count > 0 or counts["snapshot_integrity_error"] > 0:
        logger.info(
            "Academy V2 graduation parity report cohort_id=%s total=%s aligned=%s "
            "legacy_only=%s v2_only=%s integrity_err=%s",
            cohort.id,
            total_enrollments,
            aligned_count,
            counts["legacy_only_eligible"],
            counts["v2_only_eligible"],
            counts["snapshot_integrity_error"],
        )

    return {
        "advisory_only": True,
        "legacy_consumer_active": True,
        "evaluated_at": now,
        "cohort_id": str(cohort.id),
        "cohort_name": cohort.name,
        "cohort_status": cohort.status.value,
        "program_id": str(cohort.program_id),
        "program_name": program.name if program else None,
        "has_frozen_snapshot": has_frozen_snapshot,
        "snapshot_integrity_valid": snapshot_integrity_valid,
        "snapshot_sha256": snapshot.sha256 if snapshot else None,
        "snapshot_frozen_at": snapshot.created_at if snapshot else None,
        "legacy_total_milestones": legacy_total,
        "v2_required_milestones_count": len(required_v2_milestones),
        "v2_safety_gate_count": safety_gate_count,
        "summary": {
            "total_enrollments": total_enrollments,
            "aligned_count": aligned_count,
            "aligned_eligible_count": counts["aligned_eligible"],
            "aligned_ineligible_count": counts["aligned_ineligible"],
            "legacy_only_eligible_count": counts["legacy_only_eligible"],
            "v2_only_eligible_count": counts["v2_only_eligible"],
            "no_frozen_snapshot_count": counts["no_frozen_snapshot"],
            "snapshot_integrity_error_count": counts["snapshot_integrity_error"],
            "discrepancy_count": discrepancy_count,
            "parity_rate_percent": parity_rate_percent,
            "strict_parity_holds": bool(
                snapshot_usable and total_enrollments > 0 and discrepancy_count == 0
            ),
            "zero_legacy_regression": bool(
                snapshot_usable
                and total_enrollments > 0
                and counts["legacy_only_eligible"] == 0
            ),
        },
        "enrollments": rows,
    }


def _select_best_verification(
    views: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select highest-precedence verification (verified > expired > recheck > revoked).

    Within the same precedence, input records are already ordered most-recent first.
    """
    return max(views, key=lambda item: STATUS_PRIORITY.get(item["status"], -1))


def _serialize_advisory_item(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "verification_id": view["id"],
        "milestone_definition_id": view["milestone_definition_id"],
        "milestone_version_id": view["milestone_version_id"],
        "code": view["code"],
        "title": view["title"],
        "safety_gate": bool(view.get("safety_gate", False)),
        "status": view["status"],
        "status_reason": view["status_reason"],
        "source_type": view["source_type"],
        "assessor_role": view["assessor_role"],
        "verified_at": view["verified_at"],
        "expires_at": view["expires_at"],
        "revoked_at": view["revoked_at"],
    }


async def compute_club_readiness_advisory(
    db: AsyncSession,
    member_auth_id: str,
) -> dict[str, Any]:
    """Build a read-only Club-readiness competency advisory from a member's V2 passport.

    Strictly advisory evidence for human reviewers (ADR-005):
    never modifies ClubApplication, never unlocks Club checkout, and never
    bypasses first-session live verification rules.
    """
    now = utc_now()
    records = (
        (
            await db.execute(
                select(AcademyCompetencyVerification)
                .where(AcademyCompetencyVerification.member_auth_id == member_auth_id)
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
    views = await project_verifications(db, records, include_safety_gate=True)

    by_definition: dict[str, list[dict[str, Any]]] = {}
    by_code: dict[str, list[dict[str, Any]]] = {}
    for view in views:
        by_definition.setdefault(view["milestone_definition_id"], []).append(view)
        if view["code"]:
            by_code.setdefault(view["code"], []).append(view)

    verified_competencies: list[dict[str, Any]] = []
    verified_safety_gates: list[dict[str, Any]] = []
    non_verified_alerts: list[dict[str, Any]] = []
    expired_or_recheck_count = 0
    revoked_count = 0

    for def_views in by_definition.values():
        best = _select_best_verification(def_views)
        item = _serialize_advisory_item(best)
        if best["status"] == "verified":
            verified_competencies.append(item)
            if item["safety_gate"]:
                verified_safety_gates.append(item)
        else:
            non_verified_alerts.append(item)
            if best["status"] == "revoked":
                revoked_count += 1
            else:
                expired_or_recheck_count += 1

    verified_competencies.sort(
        key=lambda item: (not item["safety_gate"], item["code"] or "")
    )
    verified_safety_gates.sort(key=lambda item: item["code"] or "")
    non_verified_alerts.sort(
        key=lambda item: (not item["safety_gate"], item["code"] or "")
    )

    core_club_checks: list[dict[str, Any]] = []
    for spec in CLUB_CORE_CHECKS:
        code = spec["milestone_code"]
        matching = by_code.get(code, [])
        if matching:
            best = _select_best_verification(matching)
            core_club_checks.append(
                {
                    "check_key": spec["check_key"],
                    "milestone_code": code,
                    "label": spec["label"],
                    "required_for_club_profile": spec["required_for_club_profile"],
                    "status": best["status"],
                    "status_reason": best["status_reason"],
                    "verification_id": best["id"],
                    "source_type": best["source_type"],
                    "assessor_role": best["assessor_role"],
                    "verified_at": best["verified_at"],
                    "expires_at": best["expires_at"],
                }
            )
        else:
            core_club_checks.append(
                {
                    "check_key": spec["check_key"],
                    "milestone_code": code,
                    "label": spec["label"],
                    "required_for_club_profile": spec["required_for_club_profile"],
                    "status": "not_verified",
                    "status_reason": None,
                    "verification_id": None,
                    "source_type": None,
                    "assessor_role": None,
                    "verified_at": None,
                    "expires_at": None,
                }
            )

    required_core_met = all(
        check["status"] == "verified"
        for check in core_club_checks
        if check["required_for_club_profile"]
    )
    if required_core_met:
        advisory_signal = "meets_club_safety_profile"
    elif verified_competencies:
        advisory_signal = "partial_evidence"
    elif non_verified_alerts:
        advisory_signal = "renewal_or_recheck_needed"
    else:
        advisory_signal = "no_v2_evidence"

    return {
        "advisory_only": True,
        "human_approval_required": True,
        "advisory_note": CLUB_ADVISORY_NOTE,
        "member_auth_id": member_auth_id,
        "evaluated_at": now,
        "advisory_signal": advisory_signal,
        "core_club_checks": core_club_checks,
        "verified_safety_gates": verified_safety_gates,
        "verified_competencies": verified_competencies,
        "non_verified_alerts": non_verified_alerts,
        "counts": {
            "verified_total": len(verified_competencies),
            "verified_safety_gates": len(verified_safety_gates),
            "expired_or_recheck": expired_or_recheck_count,
            "revoked": revoked_count,
        },
    }


async def _missing_required_cohort_quizzes(
    db: AsyncSession,
    *,
    cohort_id: uuid.UUID | None,
    enrollment_id: uuid.UUID,
) -> set[uuid.UUID]:
    if cohort_id is None:
        return set()
    required_quizzes = set(
        (
            await db.execute(
                select(AcademyQuizAssignment.id).where(
                    AcademyQuizAssignment.cohort_id == cohort_id,
                    AcademyQuizAssignment.required.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not required_quizzes:
        return set()
    passed_quizzes = set(
        (
            await db.execute(
                select(AcademyQuizAttempt.assignment_id).where(
                    AcademyQuizAttempt.enrollment_id == enrollment_id,
                    AcademyQuizAttempt.passed.is_(True),
                    AcademyQuizAttempt.assignment_id.in_(required_quizzes),
                )
            )
        )
        .scalars()
        .all()
    )
    return required_quizzes - passed_quizzes


async def required_quizzes_passed_by_enrollment(
    db: AsyncSession,
    *,
    cohort_id: uuid.UUID,
    enrollment_ids: list[uuid.UUID],
) -> dict[uuid.UUID, bool]:
    """Batch-check required cohort knowledge quiz completion for a cohort (zero N+1)."""
    if not enrollment_ids:
        return {}
    required_quizzes = set(
        (
            await db.execute(
                select(AcademyQuizAssignment.id).where(
                    AcademyQuizAssignment.cohort_id == cohort_id,
                    AcademyQuizAssignment.required.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not required_quizzes:
        return {eid: True for eid in enrollment_ids}
    passed_rows = (
        await db.execute(
            select(
                AcademyQuizAttempt.enrollment_id,
                AcademyQuizAttempt.assignment_id,
            ).where(
                AcademyQuizAttempt.enrollment_id.in_(enrollment_ids),
                AcademyQuizAttempt.passed.is_(True),
                AcademyQuizAttempt.assignment_id.in_(required_quizzes),
            )
        )
    ).all()
    passed_by_enrollment: dict[uuid.UUID, set[uuid.UUID]] = {}
    for eid, assignment_id in passed_rows:
        passed_by_enrollment.setdefault(eid, set()).add(assignment_id)
    return {
        eid: required_quizzes.issubset(passed_by_enrollment.get(eid, set()))
        for eid in enrollment_ids
    }


async def evaluate_enrollment_certificate_eligibility(
    db: AsyncSession,
    enrollment: Enrollment,
    *,
    mode: GraduationMode | None = None,
    check_quizzes: bool = True,
) -> dict[str, Any]:
    """Single authoritative eligibility evaluator for certificate downloads & issuance.

    - In 'legacy' or 'shadow_log' mode (or when the cohort has no frozen V2 snapshot),
      evaluates legacy StudentProgress milestones + optional required cohort quizzes.
    - In 'v2_enforced' mode for a cohort with a frozen V2 snapshot, fails closed on
      snapshot integrity errors, requires live verified V2 credentials for every
      required milestone and safety_gate (recognizing prior/diagnostic credit and
      failing closed on expiry/revocation), and checks required cohort quizzes.
    """
    effective_mode = mode or get_graduation_mode()

    snapshot = (
        (
            await db.execute(
                select(AcademyCohortRequirementsSnapshot).where(
                    AcademyCohortRequirementsSnapshot.cohort_id == enrollment.cohort_id
                )
            )
        ).scalar_one_or_none()
        if enrollment.cohort_id
        else None
    )

    if effective_mode == "v2_enforced" and snapshot is not None:
        if (
            snapshot.program_id != enrollment.program_id
            or requirements_sha256(snapshot.requirements_json) != snapshot.sha256
        ):
            return {
                "eligible": False,
                "mode_used": "v2_enforced",
                "reason": (
                    "Certificate eligibility suspended: frozen cohort requirements "
                    "integrity check failed"
                ),
            }

        raw_milestones = snapshot.requirements_json.get("milestones", [])
        required_milestones = [
            m
            for m in raw_milestones
            if bool(m.get("required_for_completion")) or bool(m.get("safety_gate"))
        ]
        if not required_milestones or not enrollment.member_auth_id:
            return {
                "eligible": False,
                "mode_used": "v2_enforced",
                "reason": (
                    "Certificate eligibility suspended: required V2 competency "
                    "credentials are incomplete"
                ),
            }

        records = (
            (
                await db.execute(
                    select(AcademyCompetencyVerification)
                    .where(
                        AcademyCompetencyVerification.member_auth_id
                        == str(enrollment.member_auth_id)
                    )
                    .order_by(
                        AcademyCompetencyVerification.verified_at.desc(),
                        AcademyCompetencyVerification.id.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        projected = await project_verifications(db, records)
        verified_def_ids = {
            v["milestone_definition_id"] for v in projected if v["status"] == "verified"
        }
        missing_codes = [
            str(m.get("code") or m.get("definition_id"))
            for m in required_milestones
            if str(m.get("definition_id") or "") not in verified_def_ids
        ]
        if missing_codes:
            return {
                "eligible": False,
                "mode_used": "v2_enforced",
                "reason": (
                    "Certificate eligibility suspended: required V2 competency "
                    "credential is missing, expired, or revoked"
                ),
                "missing_v2_codes": missing_codes,
            }

        if check_quizzes:
            missing_quizzes = await _missing_required_cohort_quizzes(
                db, cohort_id=enrollment.cohort_id, enrollment_id=enrollment.id
            )
            if missing_quizzes:
                return {
                    "eligible": False,
                    "mode_used": "v2_enforced",
                    "reason": (
                        "Certificate eligibility suspended: knowledge quizzes are "
                        "incomplete"
                    ),
                }

        return {"eligible": True, "mode_used": "v2_enforced", "reason": None}

    # Legacy evaluation (or safe fallback for unfrozen cohorts)
    expected_milestones = set(
        (
            await db.execute(
                select(Milestone.id).where(
                    Milestone.program_id == enrollment.program_id
                )
            )
        )
        .scalars()
        .all()
    )
    achieved_milestones = set(
        (
            await db.execute(
                select(StudentProgress.milestone_id).where(
                    StudentProgress.enrollment_id == enrollment.id,
                    StudentProgress.status == ProgressStatus.ACHIEVED,
                )
            )
        )
        .scalars()
        .all()
    )
    if not expected_milestones or not expected_milestones.issubset(achieved_milestones):
        return {
            "eligible": False,
            "mode_used": "legacy",
            "reason": (
                "Certificate eligibility suspended: a practical milestone is not "
                "verified"
            ),
        }

    if check_quizzes:
        missing_quizzes = await _missing_required_cohort_quizzes(
            db, cohort_id=enrollment.cohort_id, enrollment_id=enrollment.id
        )
        if missing_quizzes:
            return {
                "eligible": False,
                "mode_used": "legacy",
                "reason": (
                    "Certificate eligibility suspended: knowledge quizzes are "
                    "incomplete"
                ),
            }

    return {"eligible": True, "mode_used": "legacy", "reason": None}
