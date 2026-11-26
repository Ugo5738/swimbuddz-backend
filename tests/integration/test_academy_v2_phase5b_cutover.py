"""Integration tests for Phase 5B: feature-flagged V2 graduation cutover & Stroke Lab TI drill bridge."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

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
    AcademyDrill,
    AcademyDrillVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    AcademyStudentDrillAssignment,
    CohortStatus,
    Enrollment,
    Milestone,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.routers.drill_assignments import (
    STROKELAB_ACADEMY_REF_TO_DRILL_CODE,
)
from services.academy_service.services.parity import get_graduation_mode
from services.academy_service.services.requirements_resolver import requirements_sha256
from services.academy_service.tasks.reporting import check_and_issue_certificates
from services.ai_service.analysis.drills import TI_DRILL_BANK
from tests.conftest import (
    make_admin_user,
    make_coach_user,
    make_member_user,
    make_service_role_user,
)
from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory


def _as_user(user):
    academy_app.dependency_overrides.pop(require_admin, None)
    academy_app.dependency_overrides.pop(require_service_role, None)
    academy_app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.asyncio
@pytest.mark.integration
async def test_graduation_mode_endpoint_and_default_legacy_behavior(academy_client):
    assert get_graduation_mode() == "legacy"

    _as_user(make_coach_user())
    denied = await academy_client.get("/academy/competencies/admin/graduation-mode")
    assert denied.status_code == 403

    _as_user(make_admin_user())
    resp = await academy_client.get("/academy/competencies/admin/graduation-mode")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "legacy"
    assert data["v2_enforced_active"] is False
    assert data["shadow_logging_active"] is False
    assert data["legacy_fallback_for_unfrozen_cohorts"] is True

    # Unknown env mode fails safe to 'legacy'
    fake_settings = MagicMock(ACADEMY_V2_GRADUATION_MODE="unexpected_mode")
    with patch(
        "services.academy_service.services.parity.get_settings",
        return_value=fake_settings,
    ):
        assert get_graduation_mode() == "legacy"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_certificate_download_enforces_v2_mode_and_falls_back_for_unfrozen_cohorts(
    academy_client, db_session, seed_member_row
):
    swimmer_frozen = await seed_member_row()
    swimmer_unfrozen = await seed_member_row()

    program = ProgramFactory.create()
    cohort_frozen = CohortFactory.create(
        program_id=program.id, status=CohortStatus.ACTIVE
    )
    cohort_unfrozen = CohortFactory.create(
        program_id=program.id, status=CohortStatus.ACTIVE
    )
    legacy_ms = Milestone(
        id=uuid.uuid4(),
        program_id=program.id,
        name="Legacy Final 25m",
        order_index=1,
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add_all([cohort_frozen, cohort_unfrozen, legacy_ms])
    await db_session.flush()

    now = utc_now()
    enroll_frozen = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort_frozen.id,
        member_id=swimmer_frozen.id,
        member_auth_id=swimmer_frozen.auth_id,
        certificate_issued_at=now,
        certificate_code="SB-2026-FROZEN01",
    )
    enroll_unfrozen = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort_unfrozen.id,
        member_id=swimmer_unfrozen.id,
        member_auth_id=swimmer_unfrozen.auth_id,
        certificate_issued_at=now,
        certificate_code="SB-2026-HIST0001",
    )
    db_session.add_all([enroll_frozen, enroll_unfrozen])
    await db_session.flush()

    db_session.add_all(
        [
            StudentProgress(
                enrollment_id=enroll_frozen.id,
                milestone_id=legacy_ms.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
            StudentProgress(
                enrollment_id=enroll_unfrozen.id,
                milestone_id=legacy_ms.id,
                status=ProgressStatus.ACHIEVED,
                achieved_at=now,
            ),
        ]
    )

    suffix = uuid.uuid4().hex[:8].upper()
    ms_def = AcademyMilestoneDefinition(
        id=uuid.uuid4(), code=f"MS-CUT-SAFE-{suffix}", title="V2 Safety Gate"
    )
    ms_ver = AcademyMilestoneVersion(
        id=uuid.uuid4(),
        definition_id=ms_def.id,
        version=1,
        state="published",
        published_at=now,
        created_by_auth_id=str(uuid.uuid4()),
        rubric_json={"criteria": ["continuous-25m"]},
        evidence_policy="video",
        assessment_context_json={"validity_days": 180},
        safety_gate=True,
    )
    snapshot_json = {
        "schema_version": 1,
        "cohort_id": str(cohort_frozen.id),
        "program_id": str(program.id),
        "milestones": [
            {
                "definition_id": str(ms_def.id),
                "code": ms_def.code,
                "title": ms_def.title,
                "version_id": str(ms_ver.id),
                "version_number": 1,
                "rubric_snapshot": ms_ver.rubric_json,
                "evidence_policy": "video",
                "assessment_context_snapshot": ms_ver.assessment_context_json,
                "safety_gate": True,
                "required_for_completion": True,
                "target_week": 6,
                "sources": ["program"],
            }
        ],
    }
    db_session.add(ms_def)
    await db_session.flush()
    db_session.add(ms_ver)
    await db_session.flush()
    db_session.add(
        AcademyCohortRequirementsSnapshot(
            cohort_id=cohort_frozen.id,
            program_id=program.id,
            requirements_json=snapshot_json,
            sha256=requirements_sha256(snapshot_json),
            created_by_auth_id=str(uuid.uuid4()),
        )
    )
    await db_session.commit()

    member_mock = AsyncMock(return_value={"first_name": "Ada", "last_name": "Swimmer"})
    _as_user(make_member_user(user_id=swimmer_frozen.auth_id))

    # 1. Under default 'legacy' mode, legacy milestones allow PDF download
    with patch(
        "services.academy_service.routers.reports.get_member_by_id", new=member_mock
    ):
        resp_legacy = await academy_client.get(
            f"/academy/enrollments/{enroll_frozen.id}/certificate.pdf"
        )
    assert resp_legacy.status_code == 200
    assert resp_legacy.headers["content-type"] == "application/pdf"

    # 2. Under 'v2_enforced', frozen cohort without V2 verification is blocked (409)
    enforced_settings = MagicMock(ACADEMY_V2_GRADUATION_MODE="v2_enforced")
    with (
        patch(
            "services.academy_service.services.parity.get_settings",
            return_value=enforced_settings,
        ),
        patch(
            "services.academy_service.routers.reports.get_member_by_id", new=member_mock
        ),
    ):
        resp_blocked = await academy_client.get(
            f"/academy/enrollments/{enroll_frozen.id}/certificate.pdf"
        )
    assert resp_blocked.status_code == 409
    assert "V2 competency" in resp_blocked.json()["detail"]

    # 3. Issue live diagnostic V2 credential -> PDF download succeeds under 'v2_enforced'
    verif = AcademyCompetencyVerification(
        member_id=swimmer_frozen.id,
        member_auth_id=swimmer_frozen.auth_id,
        source_type="diagnostic_assessment",
        assessor_role="coach",
        milestone_definition_id=ms_def.id,
        milestone_version_id=ms_ver.id,
        rubric_snapshot=ms_ver.rubric_json,
        criteria_passed=["continuous-25m"],
        assessment_context_snapshot=ms_ver.assessment_context_json,
        evidence_media_id=uuid.uuid4(),
        evidence_owner_auth_id=swimmer_frozen.auth_id,
        verified_by_auth_id=str(uuid.uuid4()),
        review_notes="Coach verified continuous 25m video during diagnostic clinic.",
        verified_at=now,
        expires_at=now + timedelta(days=180),
    )
    db_session.add(verif)
    await db_session.commit()

    with (
        patch(
            "services.academy_service.services.parity.get_settings",
            return_value=enforced_settings,
        ),
        patch(
            "services.academy_service.routers.reports.get_member_by_id", new=member_mock
        ),
    ):
        resp_v2_ok = await academy_client.get(
            f"/academy/enrollments/{enroll_frozen.id}/certificate.pdf"
        )
    assert resp_v2_ok.status_code == 200

    # 4. Revoke V2 credential -> immediately suspends certificate PDF download (409)
    db_session.add(
        AcademyCompetencyRevocation(
            verification_id=verif.id,
            revoked_by_auth_id=str(uuid.uuid4()),
            reason="Overturned after video audit found incomplete 25m finish.",
        )
    )
    await db_session.commit()

    with (
        patch(
            "services.academy_service.services.parity.get_settings",
            return_value=enforced_settings,
        ),
        patch(
            "services.academy_service.routers.reports.get_member_by_id", new=member_mock
        ),
    ):
        resp_revoked = await academy_client.get(
            f"/academy/enrollments/{enroll_frozen.id}/certificate.pdf"
        )
    assert resp_revoked.status_code == 409

    # 5. Historical cohort WITHOUT a frozen snapshot still works under 'v2_enforced'
    _as_user(make_member_user(user_id=swimmer_unfrozen.auth_id))
    with (
        patch(
            "services.academy_service.services.parity.get_settings",
            return_value=enforced_settings,
        ),
        patch(
            "services.academy_service.routers.reports.get_member_by_id", new=member_mock
        ),
    ):
        resp_hist = await academy_client.get(
            f"/academy/enrollments/{enroll_unfrozen.id}/certificate.pdf"
        )
    assert resp_hist.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_check_and_issue_certificates_respects_v2_enforced_mode(
    db_session, seed_member_row
):
    student_legacy_only = await seed_member_row()
    student_v2_only = await seed_member_row()

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id, status=CohortStatus.ACTIVE)
    legacy_ms = Milestone(
        id=uuid.uuid4(),
        program_id=program.id,
        name="Legacy 25m Gate",
        order_index=1,
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add_all([cohort, legacy_ms])
    await db_session.flush()

    enroll_legacy = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_legacy_only.id,
        member_auth_id=student_legacy_only.auth_id,
    )
    enroll_v2 = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student_v2_only.id,
        member_auth_id=student_v2_only.auth_id,
    )
    db_session.add_all([enroll_legacy, enroll_v2])
    await db_session.flush()

    now = utc_now()
    # student_legacy_only has legacy progress only
    db_session.add(
        StudentProgress(
            enrollment_id=enroll_legacy.id,
            milestone_id=legacy_ms.id,
            status=ProgressStatus.ACHIEVED,
            achieved_at=now,
        )
    )

    suffix = uuid.uuid4().hex[:8].upper()
    ms_def = AcademyMilestoneDefinition(
        id=uuid.uuid4(), code=f"MS-JOB-SAFE-{suffix}", title="Job V2 Safety Gate"
    )
    ms_ver = AcademyMilestoneVersion(
        id=uuid.uuid4(),
        definition_id=ms_def.id,
        version=1,
        state="published",
        published_at=now,
        created_by_auth_id=str(uuid.uuid4()),
        rubric_json={"criteria": ["continuous-25m"]},
        evidence_policy="video",
        assessment_context_json={"validity_days": 180},
        safety_gate=True,
    )
    snapshot_json = {
        "schema_version": 1,
        "cohort_id": str(cohort.id),
        "program_id": str(program.id),
        "milestones": [
            {
                "definition_id": str(ms_def.id),
                "code": ms_def.code,
                "title": ms_def.title,
                "version_id": str(ms_ver.id),
                "version_number": 1,
                "rubric_snapshot": ms_ver.rubric_json,
                "evidence_policy": "video",
                "assessment_context_snapshot": ms_ver.assessment_context_json,
                "safety_gate": True,
                "required_for_completion": True,
                "target_week": 6,
                "sources": ["program"],
            }
        ],
    }
    db_session.add(ms_def)
    await db_session.flush()
    db_session.add(ms_ver)
    await db_session.flush()
    db_session.add(
        AcademyCohortRequirementsSnapshot(
            cohort_id=cohort.id,
            program_id=program.id,
            requirements_json=snapshot_json,
            sha256=requirements_sha256(snapshot_json),
            created_by_auth_id=str(uuid.uuid4()),
        )
    )
    # student_v2_only holds V2 credential only (diagnostic prior credit)
    db_session.add(
        AcademyCompetencyVerification(
            member_id=student_v2_only.id,
            member_auth_id=student_v2_only.auth_id,
            source_type="diagnostic_assessment",
            assessor_role="coach",
            milestone_definition_id=ms_def.id,
            milestone_version_id=ms_ver.id,
            rubric_snapshot=ms_ver.rubric_json,
            criteria_passed=["continuous-25m"],
            assessment_context_snapshot=ms_ver.assessment_context_json,
            evidence_media_id=uuid.uuid4(),
            evidence_owner_auth_id=student_v2_only.auth_id,
            verified_by_auth_id=str(uuid.uuid4()),
            review_notes="Diagnostic clinic passed all safety criteria cleanly.",
            verified_at=now,
            expires_at=now + timedelta(days=180),
        )
    )
    await db_session.commit()

    async def _fake_get_async_db():
        yield db_session

    members_payload = [
        {
            "id": str(student_legacy_only.id),
            "auth_id": student_legacy_only.auth_id,
            "email": "legacy@test.com",
            "first_name": "Legacy",
            "last_name": "Only",
        },
        {
            "id": str(student_v2_only.id),
            "auth_id": student_v2_only.auth_id,
            "email": "v2@test.com",
            "first_name": "V2",
            "last_name": "Ready",
        },
    ]
    email_mock = MagicMock()
    email_mock.send_template = AsyncMock(return_value=True)
    enforced_settings = MagicMock(ACADEMY_V2_GRADUATION_MODE="v2_enforced")

    with (
        patch(
            "services.academy_service.tasks.reporting.get_async_db",
            new=_fake_get_async_db,
        ),
        patch(
            "services.academy_service.services.parity.get_settings",
            return_value=enforced_settings,
        ),
        patch(
            "services.academy_service.services.parity.get_members_bulk",
            new=AsyncMock(return_value=members_payload),
        ),
        patch(
            "services.academy_service.tasks.reporting.get_members_bulk",
            new=AsyncMock(return_value=members_payload),
        ),
        patch(
            "services.academy_service.tasks.reporting.get_completed_session_ids_for_cohort",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.academy_service.tasks.reporting.get_email_client",
            return_value=email_mock,
        ),
        patch(
            "services.academy_service.tasks.enrollment.grant_graduation_rewards",
            new=AsyncMock(return_value=None),
        ),
        patch.object(db_session, "close", new=AsyncMock()),
    ):
        await check_and_issue_certificates()

    refreshed_legacy = await db_session.get(Enrollment, enroll_legacy.id)
    refreshed_v2 = await db_session.get(Enrollment, enroll_v2.id)
    assert refreshed_legacy is not None and refreshed_v2 is not None
    assert refreshed_legacy.certificate_issued_at is None
    assert refreshed_v2.certificate_issued_at is not None
    assert refreshed_v2.certificate_code is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_strokelab_ti_drill_bridge_mapping_idempotency_and_zero_credential_leak(
    academy_client, db_session, seed_member_row
):
    # Contract check: every TI drill academy_ref in ai_service maps deterministically
    # to an Academy V2 drill code seeded in scripts/seed/academy_curriculum_v2.py.
    from scripts.seed.academy_curriculum_v2 import DRILLS as SEEDED_DRILLS

    seeded_codes = {item["code"] for item in SEEDED_DRILLS}
    for drill in TI_DRILL_BANK.values():
        ref = drill.get("academy_ref")
        assert ref in STROKELAB_ACADEMY_REF_TO_DRILL_CODE, f"Missing ref mapping: {ref}"
        assert STROKELAB_ACADEMY_REF_TO_DRILL_CODE[ref] in seeded_codes

    swimmer = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=swimmer.id,
        member_auth_id=swimmer.auth_id,
    )
    db_session.add(program)
    await db_session.flush()
    db_session.add(cohort)
    await db_session.flush()
    db_session.add(enrollment)

    # Ensure DRL-F4-SUPERMAN-GLIDE has a published version
    drill = (
        await db_session.execute(
            select(AcademyDrill).where(AcademyDrill.code == "DRL-F4-SUPERMAN-GLIDE")
        )
    ).scalar_one_or_none()
    if drill is None:
        drill = AcademyDrill(
            id=uuid.uuid4(),
            code="DRL-F4-SUPERMAN-GLIDE",
            title="TI Superman Streamline Glide",
        )
        db_session.add(drill)
        await db_session.flush()
    drill_ver = (
        await db_session.execute(
            select(AcademyDrillVersion).where(
                AcademyDrillVersion.drill_id == drill.id,
                AcademyDrillVersion.state == "published",
            )
        )
    ).scalar_one_or_none()
    if drill_ver is None:
        drill_ver = AcademyDrillVersion(
            id=uuid.uuid4(),
            drill_id=drill.id,
            version=1,
            state="published",
            published_at=utc_now(),
            created_by_auth_id=str(uuid.uuid4()),
            steps_json=["Push off in shoulder-width Superman glide", "Eyes down"],
            primary_focal_cue="Look straight down; press chest gently to float hips.",
            requires_supervision=True,
        )
        db_session.add(drill_ver)
    await db_session.commit()

    verifs_before = (
        await db_session.scalar(
            select(func.count(AcademyCompetencyVerification.id)).where(
                AcademyCompetencyVerification.member_auth_id == swimmer.auth_id
            )
        )
        or 0
    )

    _as_user(make_member_user(user_id=swimmer.auth_id))

    # Unknown academy_ref rejected fail-closed (422)
    bad = await academy_client.post(
        "/academy/competencies/me/drills/from-strokelab",
        json={"academy_ref": "academy:ti:invented-unsafe-drill"},
    )
    assert bad.status_code == 422

    # First save creates strokelab_ai assignment linked to active enrollment
    first = await academy_client.post(
        "/academy/competencies/me/drills/from-strokelab",
        json={
            "academy_ref": "academy:ti:superman-glide",
            "strokelab_job_id": "job-abc-123",
            "focal_cue_note": "Release head weight and look straight down.",
        },
    )
    assert first.status_code == 201, first.text
    first_data = first.json()
    assert first_data["already_assigned"] is False
    assert first_data["assigned_by_role"] == "strokelab_ai"
    assert first_data["assigned_by_auth_id"] == "strokelab:job-abc-123"
    assert first_data["enrollment_id"] == str(enrollment.id)
    assert first_data["drill_code"] == "DRL-F4-SUPERMAN-GLIDE"

    # Second save is idempotent (returns same assignment ID without duplicating)
    second = await academy_client.post(
        "/academy/competencies/me/drills/from-strokelab",
        json={
            "academy_ref": "academy:ti:superman-glide",
            "strokelab_job_id": "job-abc-123",
        },
    )
    assert second.status_code == 201
    assert second.json()["id"] == first_data["id"]
    assert second.json()["already_assigned"] is True

    # Internal service-role endpoint denies human admin, allows service_role
    _as_user(make_admin_user())
    denied_internal = await academy_client.post(
        "/internal/academy/competencies/drills/prescribe-from-strokelab",
        json={
            "member_auth_id": swimmer.auth_id,
            "academy_ref": "academy:ti:superman-glide",
        },
    )
    assert denied_internal.status_code == 403

    _as_user(make_service_role_user())
    ok_internal = await academy_client.post(
        "/internal/academy/competencies/drills/prescribe-from-strokelab",
        json={
            "member_auth_id": swimmer.auth_id,
            "academy_ref": "academy:ti:superman-glide",
        },
    )
    assert ok_internal.status_code == 201
    assert ok_internal.json()["id"] == first_data["id"]
    assert ok_internal.json()["already_assigned"] is True

    # Strict ADR-010 / ADR-013 invariant: AI drill prescription NEVER issues credentials
    verifs_after = (
        await db_session.scalar(
            select(func.count(AcademyCompetencyVerification.id)).where(
                AcademyCompetencyVerification.member_auth_id == swimmer.auth_id
            )
        )
        or 0
    )
    assert verifs_after == verifs_before

    total_assignments = (
        await db_session.scalar(
            select(func.count(AcademyStudentDrillAssignment.id)).where(
                AcademyStudentDrillAssignment.member_auth_id == swimmer.auth_id,
                AcademyStudentDrillAssignment.drill_version_id == drill_ver.id,
            )
        )
        or 0
    )
    assert total_assignments == 1
