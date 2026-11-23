"""Evidence-backed, portable competency passport regression tests."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from libs.auth.dependencies import get_current_user
from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyCompetencyRevocation,
    AcademyCompetencyVerification,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    MilestoneEventType,
    MilestoneReviewEvent,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.services.requirements_resolver import requirements_sha256
from tests.conftest import make_member_user
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MilestoneFactory,
    ProgramFactory,
)


async def _record_with_approved_video(db_session, seed_member_row, *, validity_days=365):
    member = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=member.id,
        member_auth_id=member.auth_id,
    )
    legacy = MilestoneFactory.create(program_id=program.id)
    definition = AcademyMilestoneDefinition(
        code="PASSPORT-" + uuid.uuid4().hex[:12].upper(),
        title="30 seconds controlled water tread",
    )
    version = AcademyMilestoneVersion(
        definition_id=definition.id if definition.id else uuid.uuid4(),
        version=1,
        state="published",
        published_at=utc_now(),
        created_by_auth_id=str(uuid.uuid4()),
        rubric_json={"criteria": ["hands-free", "thirty-seconds"]},
        evidence_policy="video",
        assessment_context_json={"validity_days": validity_days},
        safety_gate=True,
    )
    # Identity is generated on INSERT, not constructor; fix FK explicitly.
    definition.id = version.definition_id

    evidence_id = uuid.uuid4()
    progress = StudentProgress(
        enrollment_id=enrollment.id,
        milestone_id=legacy.id,
        status=ProgressStatus.ACHIEVED,
        achieved_at=utc_now(),
        reviewed_at=utc_now(),
        evidence_media_id=evidence_id,
    )
    progress.id = uuid.uuid4()
    reviewed_at = utc_now()
    claim = MilestoneReviewEvent(
        progress_id=progress.id,
        enrollment_id=enrollment.id,
        milestone_id=legacy.id,
        event_type=MilestoneEventType.CLAIMED,
        actor_id=uuid.UUID(member.auth_id),
        actor_role="student",
        new_status=ProgressStatus.PENDING,
        evidence_media_id_snapshot=evidence_id,
        created_at=reviewed_at - timedelta(minutes=1),
    )
    approved = MilestoneReviewEvent(
        progress_id=progress.id,
        enrollment_id=enrollment.id,
        milestone_id=legacy.id,
        event_type=MilestoneEventType.APPROVED,
        actor_id=uuid.uuid4(),
        actor_role="coach",
        previous_status=ProgressStatus.PENDING,
        new_status=ProgressStatus.ACHIEVED,
        evidence_media_id_snapshot=evidence_id,
        coach_notes_snapshot="Passed supervised water test",
        created_at=reviewed_at,
    )
    snapshot_json = {
        "schema_version": 1,
        "cohort_id": str(cohort.id),
        "program_id": str(program.id),
        "milestones": [{
            "code": definition.code,
            "version_id": str(version.id or uuid.uuid4()),
            "rubric_snapshot": version.rubric_json,
            "assessment_context_snapshot": version.assessment_context_json,
            "safety_gate": True,
        }],
    }
    version.id = uuid.UUID(snapshot_json["milestones"][0]["version_id"])
    snapshot = AcademyCohortRequirementsSnapshot(
        cohort_id=cohort.id,
        program_id=program.id,
        requirements_json=snapshot_json,
        sha256=requirements_sha256(snapshot_json),
        created_by_auth_id=str(uuid.uuid4()),
    )
    db_session.add_all([
        program, cohort, enrollment, legacy, definition, version,
        progress, claim, approved, snapshot,
    ])
    await db_session.commit()
    return SimpleNamespace(
        member=member, program=program, cohort=cohort, enrollment=enrollment,
        legacy=legacy, definition=definition, version=version,
        progress=progress, approved=approved, evidence_id=evidence_id,
    )


def _payload(data):
    return {
        "source_progress_id": str(data.progress.id),
        "milestone_version_id": str(data.version.id),
        "criteria_passed": ["hands-free", "thirty-seconds"],
        "equivalence_reason": "Independent evidence and legacy rubric reviewed manually.",
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_verified_passport_is_reusable_and_idempotent(
    academy_client, db_session, seed_member_row
):
    data = await _record_with_approved_video(db_session, seed_member_row)
    with patch(
        "services.academy_service.routers.competencies.internal_get",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(status_code=200),
    ) as check:
        created = await academy_client.post(
            "/academy/competencies/admin/verify-legacy",
            json=_payload(data),
        )
        assert created.status_code == 201, created.text
        check.assert_awaited_once()
        assert check.await_args.kwargs["params"]["expected_linked_id"] == (
            f"{data.enrollment.id}:{data.legacy.id}"
        )
    replay = await academy_client.post(
        "/academy/competencies/admin/verify-legacy",
        json=_payload(data),
    )
    assert replay.status_code == 409

    original = (
        await db_session.execute(select(AcademyCompetencyVerification))
    ).scalars().all()
    assert len(original) == 1
    assert original[0].source_review_event_id == data.approved.id
    assert original[0].expires_at is not None

    from services.academy_service.app.main import app
    current = app.dependency_overrides.get(get_current_user)
    async def owner():
        return make_member_user(user_id=data.member.auth_id)
    async def stranger():
        return make_member_user(user_id=str(uuid.uuid4()))
    try:
        app.dependency_overrides[get_current_user] = owner
        mine = await academy_client.get("/academy/competencies/me")
        assert mine.status_code == 200
        assert mine.json()["competencies"][0]["status"] == "verified"
        assert len(mine.json()["verifications"]) == 1

        app.dependency_overrides[get_current_user] = stranger
        other = await academy_client.get("/academy/competencies/me")
        assert other.status_code == 200
        assert other.json()["competencies"] == []
        assert other.json()["verifications"] == []
    finally:
        if current is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = current


@pytest.mark.asyncio
@pytest.mark.integration
async def test_revocation_and_source_override_remove_current_credit(
    academy_client, db_session, seed_member_row
):
    data = await _record_with_approved_video(db_session, seed_member_row)
    with patch(
        "services.academy_service.routers.competencies.internal_get",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(status_code=200),
    ):
        created = await academy_client.post(
            "/academy/competencies/admin/verify-legacy", json=_payload(data)
        )
    assert created.status_code == 201, created.text
    passport_url = f"/academy/competencies/admin/members/{data.member.auth_id}"
    initial = await academy_client.get(passport_url)
    assert initial.json()["competencies"][0]["status"] == "verified"

    # The legacy pathway can still be overridden; the passport must fail closed.
    data.progress.status = ProgressStatus.PENDING
    data.progress.achieved_at = None
    db_session.add(
        MilestoneReviewEvent(
            progress_id=data.progress.id,
            enrollment_id=data.enrollment.id,
            milestone_id=data.legacy.id,
            event_type=MilestoneEventType.OVERRIDE,
            actor_id=uuid.uuid4(),
            actor_role="admin",
            previous_status=ProgressStatus.ACHIEVED,
            new_status=ProgressStatus.PENDING,
            override_reason="Video was judged insufficient on independent review",
        )
    )
    await db_session.commit()
    after_override = await academy_client.get(passport_url)
    assert after_override.status_code == 200
    assert after_override.json()["competencies"][0]["status"] != "verified"

    revoked = await academy_client.post(
        f"/academy/competencies/admin/{created.json()['id']}/revoke",
        json={"reason": "The independent assessment was formally invalidated."},
    )
    assert revoked.status_code == 201, revoked.text
    assert revoked.json()["status"] == "revoked"
    again = await academy_client.post(
        f"/academy/competencies/admin/{created.json()['id']}/revoke",
        json={"reason": "Repeated request to invalidate an assessment."},
    )
    assert again.status_code == 409
    final = await academy_client.get(passport_url)
    assert final.json()["verifications"][0]["status"] == "revoked"
    assert await db_session.scalar(select(AcademyCompetencyRevocation.id)) is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bad_provenance_and_missing_frozen_requirements_cannot_gain_credit(
    academy_client, db_session, seed_member_row
):
    data = await _record_with_approved_video(db_session, seed_member_row)
    bad_criteria = await academy_client.post(
        "/academy/competencies/admin/verify-legacy",
        json={**_payload(data), "criteria_passed": ["hands-free"]},
    )
    assert bad_criteria.status_code == 422

    snapshot = await db_session.scalar(
        select(AcademyCohortRequirementsSnapshot).where(
            AcademyCohortRequirementsSnapshot.cohort_id == data.cohort.id
        )
    )
    # Immutable DB trigger disallows removal; instead prove a different
    # published version is rejected because it wasn't in frozen requirements.
    other_version = AcademyMilestoneVersion(
        definition_id=data.definition.id,
        version=2,
        state="published",
        published_at=utc_now(),
        created_by_auth_id=str(uuid.uuid4()),
        rubric_json={"criteria": ["hands-free", "thirty-seconds"]},
        evidence_policy="video",
        assessment_context_json={"validity_days": 365},
        safety_gate=True,
    )
    db_session.add(other_version)
    await db_session.commit()
    result = await academy_client.post(
        "/academy/competencies/admin/verify-legacy",
        json={**_payload(data), "milestone_version_id": str(other_version.id)},
    )
    assert result.status_code == 409
    assert snapshot.sha256 == requirements_sha256(snapshot.requirements_json)
    count = (await db_session.execute(select(AcademyCompetencyVerification))).scalars().all()
    assert count == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_media_verification_failure_does_not_create_credit(
    academy_client, db_session, seed_member_row
):
    data = await _record_with_approved_video(db_session, seed_member_row)
    with patch(
        "services.academy_service.routers.competencies.internal_get",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(status_code=404),
    ):
        result = await academy_client.post(
            "/academy/competencies/admin/verify-legacy", json=_payload(data)
        )
    assert result.status_code == 422
    records = (await db_session.execute(select(AcademyCompetencyVerification))).scalars().all()
    assert records == []
