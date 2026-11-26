"""Integration tests for Phase 5A: Dual-read graduation parity & Club-readiness advisory."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from libs.auth.dependencies import (
    get_current_user,
    require_admin,
    require_service_role,
)
from libs.common.datetime_utils import utc_now
from services.academy_service.app.main import app as academy_app
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.services.requirements_resolver import requirements_sha256
from tests.conftest import (
    make_admin_user,
    make_coach_user,
    make_member_user,
    make_service_role_user,
)
from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory


def _as_user(user):
    """Enforce real require_admin / require_service_role dependencies for a given user."""
    academy_app.dependency_overrides.pop(require_admin, None)
    academy_app.dependency_overrides.pop(require_service_role, None)
    academy_app.dependency_overrides[get_current_user] = lambda: user


async def _get_or_create_milestone_version(
    db_session,
    *,
    code: str,
    title: str,
    safety_gate: bool,
    criteria: list[str],
    validity_days: int | None = 365,
) -> tuple[AcademyMilestoneDefinition, AcademyMilestoneVersion]:
    definition = (
        await db_session.execute(
            select(AcademyMilestoneDefinition).where(
                AcademyMilestoneDefinition.code == code
            )
        )
    ).scalar_one_or_none()
    if definition is None:
        definition = AcademyMilestoneDefinition(
            id=uuid.uuid4(),
            code=code,
            title=title,
        )
        db_session.add(definition)
        await db_session.flush()

    version = (
        await db_session.execute(
            select(AcademyMilestoneVersion)
            .where(
                AcademyMilestoneVersion.definition_id == definition.id,
                AcademyMilestoneVersion.state == "published",
            )
            .order_by(AcademyMilestoneVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        context = {"validity_days": validity_days} if validity_days else {}
        version = AcademyMilestoneVersion(
            id=uuid.uuid4(),
            definition_id=definition.id,
            version=1,
            state="published",
            published_at=utc_now(),
            created_by_auth_id=str(uuid.uuid4()),
            rubric_json={"criteria": criteria},
            evidence_policy="video",
            assessment_context_json=context,
            safety_gate=safety_gate,
        )
        db_session.add(version)
        await db_session.flush()

    return definition, version


def _make_snapshot_milestone(
    definition: AcademyMilestoneDefinition,
    version: AcademyMilestoneVersion,
    *,
    required: bool,
    target_week: int = 4,
) -> dict:
    return {
        "definition_id": str(definition.id),
        "code": definition.code,
        "title": definition.title,
        "version_id": str(version.id),
        "version_number": version.version,
        "rubric_snapshot": version.rubric_json,
        "evidence_policy": version.evidence_policy,
        "assessment_context_snapshot": version.assessment_context_json,
        "safety_gate": bool(version.safety_gate),
        "required_for_completion": bool(required or version.safety_gate),
        "target_week": target_week,
        "sources": ["program"],
    }


def _make_verification(
    *,
    member_id: uuid.UUID,
    member_auth_id: str,
    definition: AcademyMilestoneDefinition,
    version: AcademyMilestoneVersion,
    source_type: str = "cohort_v2",
    source_enrollment_id: uuid.UUID | None = None,
    verified_at=None,
    expires_at=None,
) -> AcademyCompetencyVerification:
    now = verified_at or utc_now()
    return AcademyCompetencyVerification(
        member_id=member_id,
        member_auth_id=member_auth_id,
        source_type=source_type,
        assessor_role="coach",
        source_enrollment_id=source_enrollment_id,
        source_progress_id=None,
        source_review_event_id=None,
        milestone_definition_id=definition.id,
        milestone_version_id=version.id,
        rubric_snapshot=version.rubric_json,
        criteria_passed=list(version.rubric_json["criteria"]),
        assessment_context_snapshot=dict(version.assessment_context_json),
        evidence_media_id=uuid.uuid4(),
        evidence_owner_auth_id=member_auth_id,
        verified_by_auth_id=str(uuid.uuid4()),
        review_notes="Coach observed clean execution on full video.",
        verified_at=now,
        expires_at=expires_at,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_graduation_parity_without_frozen_snapshot_fails_closed(
    academy_client, db_session, seed_member_row
):
    student = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student.id,
        member_auth_id=student.auth_id,
    )
    legacy_ms = Milestone(
        id=uuid.uuid4(),
        program_id=program.id,
        name="Legacy 25m Swim",
        order_index=1,
    )
    db_session.add_all([program])
    await db_session.flush()
    db_session.add_all([cohort, legacy_ms])
    await db_session.flush()
    db_session.add(enrollment)
    await db_session.flush()
    db_session.add(
        StudentProgress(
            enrollment_id=enrollment.id,
            milestone_id=legacy_ms.id,
            status=ProgressStatus.ACHIEVED,
            achieved_at=utc_now(),
        )
    )
    await db_session.commit()

    # Non-admin denied
    _as_user(make_coach_user())
    denied = await academy_client.get(
        f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
    )
    assert denied.status_code == 403

    # Admin gets fail-closed no_frozen_snapshot report
    _as_user(make_admin_user())
    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(return_value=[]),
    ):
        resp = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["advisory_only"] is True
    assert body["legacy_consumer_active"] is True
    assert body["has_frozen_snapshot"] is False
    assert body["snapshot_integrity_valid"] is False
    assert body["summary"]["total_enrollments"] == 1
    assert body["summary"]["no_frozen_snapshot_count"] == 1
    assert body["summary"]["parity_rate_percent"] is None
    assert body["summary"]["strict_parity_holds"] is False
    assert body["summary"]["zero_legacy_regression"] is False
    row = body["enrollments"][0]
    assert row["legacy_eligible"] is True
    assert row["v2_eligible"] is False
    assert row["parity_status"] == "no_frozen_snapshot"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_graduation_parity_all_four_outcomes_and_zero_mutation(
    academy_client, db_session, seed_member_row
):
    student_a = await seed_member_row()  # aligned_eligible
    student_b = await seed_member_row()  # aligned_ineligible
    student_c = await seed_member_row()  # legacy_only_eligible
    student_d = (
        await seed_member_row()
    )  # v2_only_eligible (via diagnostic prior credit)
    student_dropped = await seed_member_row()  # excluded by default

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    legacy_ms_1 = Milestone(
        id=uuid.uuid4(), program_id=program.id, name="Legacy Entry", order_index=1
    )
    legacy_ms_2 = Milestone(
        id=uuid.uuid4(), program_id=program.id, name="Legacy 25m", order_index=2
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add_all([cohort, legacy_ms_1, legacy_ms_2])
    await db_session.flush()

    enroll_a = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_a.id,
        member_auth_id=student_a.auth_id,
    )
    enroll_b = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_b.id,
        member_auth_id=student_b.auth_id,
    )
    enroll_c = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_c.id,
        member_auth_id=student_c.auth_id,
    )
    enroll_d = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_d.id,
        member_auth_id=student_d.auth_id,
    )
    enroll_dropped = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_dropped.id,
        member_auth_id=student_dropped.auth_id,
        status=EnrollmentStatus.DROPPED,
    )
    db_session.add_all([enroll_a, enroll_b, enroll_c, enroll_d, enroll_dropped])
    await db_session.flush()

    suffix = uuid.uuid4().hex[:8].upper()
    safe_def, safe_ver = await _get_or_create_milestone_version(
        db_session,
        code=f"MS-PAR-SAFE-{suffix}",
        title="Safety Gate 25m",
        safety_gate=True,
        criteria=["continuous-25m", "calm-recovery"],
        validity_days=180,
    )
    skill_def, skill_ver = await _get_or_create_milestone_version(
        db_session,
        code=f"MS-PAR-SKILL-{suffix}",
        title="Streamline Glide",
        safety_gate=False,
        criteria=["neutral-head", "glide-7m"],
        validity_days=365,
    )
    opt_def, opt_ver = await _get_or_create_milestone_version(
        db_session,
        code=f"MS-PAR-OPT-{suffix}",
        title="Optional Flip Turn Preview",
        safety_gate=False,
        criteria=["tuck-turn"],
        validity_days=None,
    )

    snapshot_json = {
        "schema_version": 1,
        "cohort_id": str(cohort.id),
        "program_id": str(program.id),
        "milestones": [
            _make_snapshot_milestone(safe_def, safe_ver, required=True, target_week=6),
            _make_snapshot_milestone(
                skill_def, skill_ver, required=True, target_week=4
            ),
            _make_snapshot_milestone(opt_def, opt_ver, required=False, target_week=8),
        ],
    }
    snapshot = AcademyCohortRequirementsSnapshot(
        cohort_id=cohort.id,
        program_id=program.id,
        requirements_json=snapshot_json,
        sha256=requirements_sha256(snapshot_json),
        created_by_auth_id=str(uuid.uuid4()),
    )
    db_session.add(snapshot)

    now = utc_now()
    future = now + timedelta(days=120)

    # Legacy achievements: A (both), B (only 1), C (both), D (none)
    db_session.add_all(
        [
            StudentProgress(
                enrollment_id=enroll_a.id,
                milestone_id=legacy_ms_1.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
            StudentProgress(
                enrollment_id=enroll_a.id,
                milestone_id=legacy_ms_2.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
            StudentProgress(
                enrollment_id=enroll_b.id,
                milestone_id=legacy_ms_1.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
            StudentProgress(
                enrollment_id=enroll_c.id,
                milestone_id=legacy_ms_1.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
            StudentProgress(
                enrollment_id=enroll_c.id,
                milestone_id=legacy_ms_2.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
        ]
    )

    # V2 verifications:
    # A has both required via current cohort (no optional needed)
    # B has only skill_def (missing safety gate)
    # C has none
    # D has both required via diagnostic prior credit
    db_session.add_all(
        [
            _make_verification(
                member_id=student_a.id,
                member_auth_id=student_a.auth_id,
                definition=safe_def,
                version=safe_ver,
                source_type="cohort_v2",
                source_enrollment_id=enroll_a.id,
                expires_at=future,
            ),
            _make_verification(
                member_id=student_a.id,
                member_auth_id=student_a.auth_id,
                definition=skill_def,
                version=skill_ver,
                source_type="cohort_v2",
                source_enrollment_id=enroll_a.id,
                expires_at=future,
            ),
            _make_verification(
                member_id=student_b.id,
                member_auth_id=student_b.auth_id,
                definition=skill_def,
                version=skill_ver,
                source_type="cohort_v2",
                source_enrollment_id=enroll_b.id,
                expires_at=future,
            ),
            _make_verification(
                member_id=student_d.id,
                member_auth_id=student_d.auth_id,
                definition=safe_def,
                version=safe_ver,
                source_type="diagnostic_assessment",
                source_enrollment_id=None,
                expires_at=future,
            ),
            _make_verification(
                member_id=student_d.id,
                member_auth_id=student_d.auth_id,
                definition=skill_def,
                version=skill_ver,
                source_type="diagnostic_assessment",
                source_enrollment_id=None,
                expires_at=future,
            ),
        ]
    )
    await db_session.commit()

    academy_app.dependency_overrides[get_current_user] = lambda: make_admin_user()
    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(
            return_value=[
                {"id": str(student_a.id), "first_name": "Ada", "last_name": "Alpha"},
                {"id": str(student_b.id), "first_name": "Bola", "last_name": "Beta"},
            ]
        ),
    ):
        resp = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_frozen_snapshot"] is True
    assert data["snapshot_integrity_valid"] is True
    assert data["legacy_total_milestones"] == 2
    assert data["v2_required_milestones_count"] == 2
    assert data["v2_safety_gate_count"] == 1

    summary = data["summary"]
    assert summary["total_enrollments"] == 4
    assert summary["aligned_count"] == 2
    assert summary["aligned_eligible_count"] == 1
    assert summary["aligned_ineligible_count"] == 1
    assert summary["legacy_only_eligible_count"] == 1
    assert summary["v2_only_eligible_count"] == 1
    assert summary["discrepancy_count"] == 2
    assert summary["parity_rate_percent"] == 50.0
    assert summary["strict_parity_holds"] is False
    assert summary["zero_legacy_regression"] is False

    by_enrollment = {row["enrollment_id"]: row for row in data["enrollments"]}
    assert str(enroll_dropped.id) not in by_enrollment

    row_a = by_enrollment[str(enroll_a.id)]
    assert row_a["member_name"] == "Ada Alpha"
    assert row_a["parity_status"] == "aligned_eligible"
    assert row_a["legacy_eligible"] is True
    assert row_a["v2_eligible"] is True
    assert row_a["v2_prior_credit_codes"] == []

    row_b = by_enrollment[str(enroll_b.id)]
    assert row_b["parity_status"] == "aligned_ineligible"
    assert row_b["v2_missing_safety_gate_codes"] == [safe_def.code]
    assert row_b["legacy_missing_milestones"] == ["Legacy 25m"]

    row_c = by_enrollment[str(enroll_c.id)]
    assert row_c["parity_status"] == "legacy_only_eligible"
    assert row_c["legacy_eligible"] is True
    assert row_c["v2_eligible"] is False
    assert safe_def.code in row_c["v2_missing_safety_gate_codes"]

    row_d = by_enrollment[str(enroll_d.id)]
    assert row_d["parity_status"] == "v2_only_eligible"
    assert row_d["legacy_eligible"] is False
    assert row_d["v2_eligible"] is True
    assert set(row_d["v2_prior_credit_codes"]) == {safe_def.code, skill_def.code}

    # include_inactive=true surfaces dropped enrollments too
    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(return_value=[]),
    ):
        resp_all = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity",
            params={"include_inactive": "true"},
        )
    assert resp_all.status_code == 200
    assert resp_all.json()["summary"]["total_enrollments"] == 5

    # Strict read-only invariant: zero mutation to enrollments or certificates
    for eid in (enroll_a.id, enroll_b.id, enroll_c.id, enroll_d.id):
        fresh = await db_session.get(Enrollment, eid)
        assert fresh is not None
        assert fresh.status == EnrollmentStatus.ENROLLED
        assert fresh.certificate_issued_at is None
        assert fresh.certificate_code is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_graduation_parity_expired_and_revoked_safety_gate_fail_closed(
    academy_client, db_session, seed_member_row
):
    student = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    legacy_ms = Milestone(
        id=uuid.uuid4(), program_id=program.id, name="Legacy Safety", order_index=1
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add_all([cohort, legacy_ms])
    await db_session.flush()

    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student.id,
        member_auth_id=student.auth_id,
    )
    db_session.add(enrollment)
    await db_session.flush()

    suffix = uuid.uuid4().hex[:8].upper()
    safe_def, safe_ver = await _get_or_create_milestone_version(
        db_session,
        code=f"MS-EXP-SAFE-{suffix}",
        title="Deep-Water Safety",
        safety_gate=True,
        criteria=["tread-60s"],
        validity_days=180,
    )
    snapshot_json = {
        "schema_version": 1,
        "cohort_id": str(cohort.id),
        "program_id": str(program.id),
        "milestones": [_make_snapshot_milestone(safe_def, safe_ver, required=True)],
    }
    db_session.add(
        AcademyCohortRequirementsSnapshot(
            cohort_id=cohort.id,
            program_id=program.id,
            requirements_json=snapshot_json,
            sha256=requirements_sha256(snapshot_json),
            created_by_auth_id=str(uuid.uuid4()),
        )
    )
    now = utc_now()
    db_session.add(
        StudentProgress(
            enrollment_id=enrollment.id,
            milestone_id=legacy_ms.id,
            status=ProgressStatus.ACHIEVED,
            achieved_at=now,
        )
    )

    # Step 1: Expired safety gate -> fail-closed legacy_only_eligible
    expired_v = _make_verification(
        member_id=student.id,
        member_auth_id=student.auth_id,
        definition=safe_def,
        version=safe_ver,
        source_type="cohort_v2",
        source_enrollment_id=enrollment.id,
        verified_at=now - timedelta(days=200),
        expires_at=now - timedelta(days=20),
    )
    db_session.add(expired_v)
    await db_session.commit()

    academy_app.dependency_overrides[get_current_user] = lambda: make_admin_user()
    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(return_value=[]),
    ):
        resp_exp = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
        )
    assert resp_exp.status_code == 200
    row_exp = resp_exp.json()["enrollments"][0]
    assert row_exp["parity_status"] == "legacy_only_eligible"
    assert row_exp["v2_eligible"] is False
    assert row_exp["v2_missing_safety_gate_codes"] == [safe_def.code]
    assert row_exp["v2_blocked_details"][0]["status"] == "expired"

    # Step 2: Renewed valid verification -> aligned_eligible + 100% strict parity
    fresh_v = _make_verification(
        member_id=student.id,
        member_auth_id=student.auth_id,
        definition=safe_def,
        version=safe_ver,
        source_type="cohort_v2",
        source_enrollment_id=enrollment.id,
        verified_at=now,
        expires_at=now + timedelta(days=180),
    )
    db_session.add(fresh_v)
    await db_session.commit()

    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(return_value=[]),
    ):
        resp_ok = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
        )
    assert resp_ok.status_code == 200
    body_ok = resp_ok.json()
    assert body_ok["summary"]["strict_parity_holds"] is True
    assert body_ok["summary"]["parity_rate_percent"] == 100.0
    assert body_ok["enrollments"][0]["parity_status"] == "aligned_eligible"

    # Step 3: Revoke renewed verification -> fails closed again
    db_session.add(
        AcademyCompetencyRevocation(
            verification_id=fresh_v.id,
            revoked_by_auth_id=str(uuid.uuid4()),
            reason="Video showed swimmer touching pool floor during tread test.",
        )
    )
    await db_session.commit()

    with patch(
        "services.academy_service.services.parity.get_members_bulk",
        new=AsyncMock(return_value=[]),
    ):
        resp_rev = await academy_client.get(
            f"/academy/competencies/admin/cohorts/{cohort.id}/graduation-parity"
        )
    assert resp_rev.status_code == 200
    assert resp_rev.json()["enrollments"][0]["parity_status"] == "legacy_only_eligible"
    assert resp_rev.json()["enrollments"][0]["v2_eligible"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_readiness_advisory_signals_expiry_revocation_and_internal_auth(
    academy_client, db_session, seed_member_row
):
    swimmer = await seed_member_row()
    lapsed_swimmer = await seed_member_row()

    # Non-admin denied on admin advisory endpoint
    _as_user(make_member_user(user_id=swimmer.auth_id))
    denied_admin = await academy_client.get(
        f"/academy/competencies/admin/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert denied_admin.status_code == 403

    # Human admin gets no_v2_evidence initially
    _as_user(make_admin_user())
    resp_empty = await academy_client.get(
        f"/academy/competencies/admin/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_empty.status_code == 200, resp_empty.text
    empty_data = resp_empty.json()
    assert empty_data["advisory_only"] is True
    assert empty_data["human_approval_required"] is True
    assert empty_data["advisory_signal"] == "no_v2_evidence"
    assert empty_data["counts"]["verified_total"] == 0
    assert all(c["status"] == "not_verified" for c in empty_data["core_club_checks"])

    # Human admin denied on internal service-role endpoint
    denied_internal = await academy_client.get(
        f"/internal/academy/competencies/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert denied_internal.status_code == 403

    # Seed canonical Club-relevant V2 safety gates
    free25_def, free25_ver = await _get_or_create_milestone_version(
        db_session,
        code="MS-S1-FREE-25M",
        title="25-Meter Continuous Freestyle with Side Breathing",
        safety_gate=True,
        criteria=["continuous-25m-unassisted", "rhythmic-side-breathing"],
        validity_days=365,
    )
    tread60_def, tread60_ver = await _get_or_create_milestone_version(
        db_session,
        code="MS-A1-TREAD-60S",
        title="60-Second Calm Deep-Water Tread & Self-Rescue Recovery",
        safety_gate=True,
        criteria=["deep-water-no-floor-contact", "sixty-second-vertical-tread"],
        validity_days=180,
    )

    now = utc_now()
    # Verify only 25m freestyle -> partial_evidence
    v_free = _make_verification(
        member_id=swimmer.id,
        member_auth_id=swimmer.auth_id,
        definition=free25_def,
        version=free25_ver,
        source_type="diagnostic_assessment",
        expires_at=now + timedelta(days=300),
    )
    db_session.add(v_free)
    await db_session.commit()

    resp_partial = await academy_client.get(
        f"/academy/competencies/admin/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_partial.status_code == 200
    partial = resp_partial.json()
    assert partial["advisory_signal"] == "partial_evidence"
    assert partial["counts"]["verified_safety_gates"] == 1
    checks_partial = {c["check_key"]: c for c in partial["core_club_checks"]}
    assert checks_partial["continuous_25m"]["status"] == "verified"
    assert checks_partial["deep_water_tread"]["status"] == "not_verified"

    # Verify 60s tread too -> meets_club_safety_profile
    v_tread = _make_verification(
        member_id=swimmer.id,
        member_auth_id=swimmer.auth_id,
        definition=tread60_def,
        version=tread60_ver,
        source_type="diagnostic_assessment",
        expires_at=now + timedelta(days=150),
    )
    db_session.add(v_tread)
    await db_session.commit()

    resp_ready = await academy_client.get(
        f"/academy/competencies/admin/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_ready.status_code == 200
    ready = resp_ready.json()
    assert ready["advisory_signal"] == "meets_club_safety_profile"
    assert ready["counts"]["verified_safety_gates"] == 2
    assert ready["human_approval_required"] is True

    # Internal service-role endpoint returns matching payload
    _as_user(make_service_role_user())
    resp_internal = await academy_client.get(
        f"/internal/academy/competencies/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_internal.status_code == 200
    assert resp_internal.json()["advisory_signal"] == "meets_club_safety_profile"
    assert resp_internal.json()["counts"]["verified_safety_gates"] == 2

    # Revoke tread verification -> immediately drops out of safety profile + raises alert
    db_session.add(
        AcademyCompetencyRevocation(
            verification_id=v_tread.id,
            revoked_by_auth_id=str(uuid.uuid4()),
            reason="Reviewer overturned tread clip after depth check.",
        )
    )
    # Seed lapsed swimmer with only an expired 25m credential -> renewal_or_recheck_needed
    db_session.add(
        _make_verification(
            member_id=lapsed_swimmer.id,
            member_auth_id=lapsed_swimmer.auth_id,
            definition=free25_def,
            version=free25_ver,
            source_type="diagnostic_assessment",
            verified_at=now - timedelta(days=400),
            expires_at=now - timedelta(days=35),
        )
    )
    await db_session.commit()

    _as_user(make_admin_user())
    resp_after_revoke = await academy_client.get(
        f"/academy/competencies/admin/members/{swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_after_revoke.status_code == 200
    revoked_data = resp_after_revoke.json()
    assert revoked_data["advisory_signal"] == "partial_evidence"
    assert revoked_data["counts"]["verified_safety_gates"] == 1
    assert revoked_data["counts"]["revoked"] == 1
    assert revoked_data["non_verified_alerts"][0]["code"] == "MS-A1-TREAD-60S"
    assert revoked_data["non_verified_alerts"][0]["status"] == "revoked"

    resp_lapsed = await academy_client.get(
        f"/academy/competencies/admin/members/{lapsed_swimmer.auth_id}/club-readiness-advisory"
    )
    assert resp_lapsed.status_code == 200
    lapsed_data = resp_lapsed.json()
    assert lapsed_data["advisory_signal"] == "renewal_or_recheck_needed"
    assert lapsed_data["counts"]["expired_or_recheck"] == 1
    assert lapsed_data["non_verified_alerts"][0]["status"] == "expired"
