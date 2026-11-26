"""Integration tests for native V2 cohort/diagnostic assessments & TI drill prescriptions."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from libs.auth.dependencies import get_current_user
from libs.common.datetime_utils import utc_now
from services.academy_service.app.main import app as academy_app
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyDrill,
    AcademyDrillVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
)
from services.academy_service.services.requirements_resolver import requirements_sha256
from tests.conftest import (
    make_coach_user,
    make_member_user,
    make_service_role_user,
)
from tests.factories import CohortFactory, EnrollmentFactory, ProgramFactory


async def _seed_v2_cohort_setup(db_session, seed_member_row):
    student = await seed_member_row()
    coach = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id, coach_id=coach.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=student.id,
        member_auth_id=student.auth_id,
    )

    ms_def = AcademyMilestoneDefinition(
        id=uuid.uuid4(),
        code="MS-V2-" + uuid.uuid4().hex[:10].upper(),
        title="25m Continuous Freestyle",
    )
    ms_ver = AcademyMilestoneVersion(
        id=uuid.uuid4(),
        definition_id=ms_def.id,
        version=1,
        state="published",
        published_at=utc_now(),
        created_by_auth_id=str(uuid.uuid4()),
        rubric_json={"criteria": ["continuous-25m", "side-breathing"]},
        evidence_policy="video",
        assessment_context_json={"validity_days": 365},
        safety_gate=True,
    )

    drill_def = AcademyDrill(
        id=uuid.uuid4(),
        code="DRL-V2-" + uuid.uuid4().hex[:10].upper(),
        title="TI Side-Lying Skate",
    )
    drill_ver = AcademyDrillVersion(
        id=uuid.uuid4(),
        drill_id=drill_def.id,
        version=1,
        state="published",
        published_at=utc_now(),
        created_by_auth_id=str(uuid.uuid4()),
        steps_json=["Push off on side", "Hold lead arm long"],
        primary_focal_cue="Weightless head aligned with spine",
        requires_supervision=True,
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
    snapshot = AcademyCohortRequirementsSnapshot(
        cohort_id=cohort.id,
        program_id=program.id,
        requirements_json=snapshot_json,
        sha256=requirements_sha256(snapshot_json),
        created_by_auth_id=str(uuid.uuid4()),
    )

    db_session.add_all([program, ms_def, drill_def])
    await db_session.flush()
    db_session.add_all([cohort, ms_ver, drill_ver])
    await db_session.flush()
    db_session.add_all([enrollment, snapshot])
    await db_session.commit()

    return SimpleNamespace(
        student=student,
        coach=coach,
        program=program,
        cohort=cohort,
        enrollment=enrollment,
        ms_def=ms_def,
        ms_ver=ms_ver,
        drill_def=drill_def,
        drill_ver=drill_ver,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_native_cohort_v2_assessment_auth_media_and_revocation(
    academy_client, db_session, seed_member_row
):
    data = await _seed_v2_cohort_setup(db_session, seed_member_row)
    evidence_id = uuid.uuid4()
    body = {
        "enrollment_id": str(data.enrollment.id),
        "milestone_version_id": str(data.ms_ver.id),
        "evidence_media_id": str(evidence_id),
        "evidence_uploaded_by": "assessor",
        "criteria_passed": ["continuous-25m", "side-breathing"],
        "assessment_notes": "Swimmer completed 25m continuously with calm side breaths.",
    }

    current = academy_app.dependency_overrides.get(get_current_user)
    try:
        # service_role cannot issue credentials
        academy_app.dependency_overrides[get_current_user] = lambda: (
            make_service_role_user("ai")
        )
        svc_resp = await academy_client.post(
            "/academy/competencies/coach/assess-cohort", json=body
        )
        assert svc_resp.status_code == 403

        # Unassigned coach is blocked
        other_coach_auth = str(uuid.uuid4())
        academy_app.dependency_overrides[get_current_user] = lambda: make_coach_user(
            user_id=other_coach_auth
        )
        with patch(
            "libs.common.service_client.get_member_by_auth_id",
            new_callable=AsyncMock,
            return_value={"id": str(uuid.uuid4()), "auth_id": other_coach_auth},
        ):
            unassigned = await academy_client.post(
                "/academy/competencies/coach/assess-cohort", json=body
            )
            assert unassigned.status_code == 403

        # Assigned cohort coach succeeds when media checks out
        academy_app.dependency_overrides[get_current_user] = lambda: make_coach_user(
            user_id=data.coach.auth_id
        )
        with (
            patch(
                "libs.common.service_client.get_member_by_auth_id",
                new_callable=AsyncMock,
                return_value={"id": str(data.coach.id), "auth_id": data.coach.auth_id},
            ),
            patch(
                "services.academy_service.routers.competencies.internal_get",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(status_code=200),
            ) as media_check,
        ):
            assessed = await academy_client.post(
                "/academy/competencies/coach/assess-cohort", json=body
            )
            assert assessed.status_code == 201, assessed.text
            payload = assessed.json()
            assert payload["source_type"] == "cohort_v2"
            assert payload["assessor_role"] == "coach"
            assert payload["status"] == "verified"
            assert payload["source_progress_id"] is None
            media_check.assert_awaited_once()
            assert media_check.await_args.kwargs["params"]["expected_linked_id"] == (
                f"{data.enrollment.id}:{data.ms_ver.id}"
            )

        # Learner sees verified credential on passport
        academy_app.dependency_overrides[get_current_user] = lambda: make_member_user(
            user_id=data.student.auth_id
        )
        passport = await academy_client.get("/academy/competencies/me")
        assert passport.status_code == 200
        assert passport.json()["competencies"][0]["status"] == "verified"
        assert passport.json()["verifications"][0]["source_type"] == "cohort_v2"
    finally:
        if current is None:
            academy_app.dependency_overrides.pop(get_current_user, None)
        else:
            academy_app.dependency_overrides[get_current_user] = current

    # Admin can still revoke a native V2 credential cleanly
    revoked = await academy_client.post(
        f"/academy/competencies/admin/{payload['id']}/revoke",
        json={"reason": "Video review audit found incomplete final 5 meters."},
    )
    assert revoked.status_code == 201
    after = await academy_client.get(
        f"/academy/competencies/admin/members/{data.student.auth_id}"
    )
    assert after.json()["competencies"][0]["status"] == "revoked"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_diagnostic_assessment_grants_prior_credit_and_remedial_drills_flow(
    academy_client, db_session, seed_member_row
):
    data = await _seed_v2_cohort_setup(db_session, seed_member_row)

    # Link remedial drill to milestone definition
    linked = await academy_client.post(
        f"/academy/admin/curriculum-library/milestones/{data.ms_def.id}/remedial-drills",
        json={
            "drill_id": str(data.drill_def.id),
            "priority_order": 0,
            "coach_tip": "Keep lead hand still during side breath.",
        },
    )
    assert linked.status_code == 201, linked.text

    current = academy_app.dependency_overrides.get(get_current_user)
    try:
        # Before verification, learner sees milestone unverified + recommended TI remedial drill
        academy_app.dependency_overrides[get_current_user] = lambda: make_member_user(
            user_id=data.student.auth_id
        )
        plan_before = await academy_client.get(
            f"/academy/competencies/enrollments/{data.enrollment.id}/v2-plan"
        )
        assert plan_before.status_code == 200, plan_before.text
        ms_before = plan_before.json()["milestones"][0]
        assert ms_before["passport_status"] == "unverified"
        assert ms_before["prior_credit"] is False
        assert len(ms_before["remedial_drills"]) == 1
        assert ms_before["remedial_drills"][0]["code"] == data.drill_def.code

        # Coach prescribes drill to learner; learner logs practiced; coach marks mastered
        academy_app.dependency_overrides[get_current_user] = lambda: make_coach_user(
            user_id=data.coach.auth_id
        )
        with patch(
            "libs.common.service_client.get_member_by_auth_id",
            new_callable=AsyncMock,
            return_value={"id": str(data.coach.id), "auth_id": data.coach.auth_id},
        ):
            published_drills = await academy_client.get(
                "/academy/competencies/drills/published"
            )
            assert published_drills.status_code == 200
            assert any(
                d["drill_version_id"] == str(data.drill_ver.id)
                for d in published_drills.json()
            )
            assigned = await academy_client.post(
                "/academy/competencies/coach/drills/assign",
                json={
                    "enrollment_id": str(data.enrollment.id),
                    "drill_version_id": str(data.drill_ver.id),
                    "milestone_definition_id": str(data.ms_def.id),
                    "focal_cue_note": "Count 6 slow kicks before switching sides.",
                    "sets": 4,
                    "distance_m": 12.5,
                },
            )
            assert assigned.status_code == 201, assigned.text
            assignment_id = assigned.json()["id"]
            assert assigned.json()["status"] == "assigned"

        # Stranger cannot touch learner's drill
        academy_app.dependency_overrides[get_current_user] = lambda: make_member_user(
            user_id=str(uuid.uuid4())
        )
        stranger = await academy_client.post(
            f"/academy/competencies/me/drills/{assignment_id}/practiced"
        )
        assert stranger.status_code == 404

        # Learner views & marks drill practiced
        academy_app.dependency_overrides[get_current_user] = lambda: make_member_user(
            user_id=data.student.auth_id
        )
        my_drills = await academy_client.get("/academy/competencies/me/drills")
        assert my_drills.status_code == 200
        assert len(my_drills.json()) == 1
        practiced = await academy_client.post(
            f"/academy/competencies/me/drills/{assignment_id}/practiced"
        )
        assert practiced.status_code == 200
        assert practiced.json()["status"] == "practiced"

        # Coach marks mastered
        academy_app.dependency_overrides[get_current_user] = lambda: make_coach_user(
            user_id=data.coach.auth_id
        )
        with patch(
            "libs.common.service_client.get_member_by_auth_id",
            new_callable=AsyncMock,
            return_value={"id": str(data.coach.id), "auth_id": data.coach.auth_id},
        ):
            mastered = await academy_client.patch(
                f"/academy/competencies/coach/drills/{assignment_id}/status",
                json={"status": "mastered"},
            )
            assert mastered.status_code == 200
            assert mastered.json()["status"] == "mastered"

        # Restore admin & record a diagnostic clinic assessment for this swimmer
        if current is None:
            academy_app.dependency_overrides.pop(get_current_user, None)
        else:
            academy_app.dependency_overrides[get_current_user] = current

        diag_media = uuid.uuid4()
        with patch(
            "services.academy_service.routers.competencies.internal_get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(status_code=200),
        ) as diag_media_check:
            diag = await academy_client.post(
                "/academy/competencies/admin/assess-diagnostic",
                json={
                    "member_id": str(data.student.id),
                    "member_auth_id": data.student.auth_id,
                    "milestone_version_id": str(data.ms_ver.id),
                    "evidence_media_id": str(diag_media),
                    "evidence_uploaded_by": "assessor",
                    "criteria_passed": ["continuous-25m", "side-breathing"],
                    "assessment_notes": "Demonstrated clean 25m freestyle at Yaba Intro-to-Water clinic.",
                    "clinic_label": "Yaba Intro Clinic Nov 2026",
                },
            )
            assert diag.status_code == 201, diag.text
            assert diag.json()["source_type"] == "diagnostic_assessment"
            assert diag.json()["source_enrollment_id"] is None
            assert (
                diag_media_check.await_args.kwargs["params"]["expected_linked_id"]
                == f"diagnostic:{data.student.id}:{data.ms_ver.id}"
            )

        # Learner's cohort V2 plan now surfaces Prior Credit and clears remedial drills
        academy_app.dependency_overrides[get_current_user] = lambda: make_member_user(
            user_id=data.student.auth_id
        )
        plan_after = await academy_client.get(
            f"/academy/competencies/enrollments/{data.enrollment.id}/v2-plan"
        )
        assert plan_after.status_code == 200
        ms_after = plan_after.json()["milestones"][0]
        assert ms_after["passport_status"] == "verified"
        assert ms_after["prior_credit"] is True
        assert ms_after["verified_source_type"] == "diagnostic_assessment"
        assert ms_after["remedial_drills"] == []
    finally:
        if current is None:
            academy_app.dependency_overrides.pop(get_current_user, None)
        else:
            academy_app.dependency_overrides[get_current_user] = current
