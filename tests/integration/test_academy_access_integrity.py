"""Regressions for Academy row-level reads and milestone claim integrity.

Run with the service integration database and Academy migrations applied.
"""

import uuid

import pytest

from libs.auth.dependencies import get_current_user
from tests.conftest import make_member_user
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MilestoneFactory,
    ProgramFactory,
)


def auth_override(user):
    """Return a predictable async FastAPI dependency override."""

    async def _resolve():
        return user

    return _resolve


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enrollment_and_progress_reads_require_owner_or_privileged_actor(
    academy_client, db_session, seed_member_row
):
    from services.academy_service.app.main import app
    from services.academy_service.models import ProgressStatus, StudentProgress

    owner = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=owner.id,
        member_auth_id=owner.auth_id,
    )
    milestone = MilestoneFactory.create(program_id=program.id)
    progress = StudentProgress(
        id=uuid.uuid4(),
        enrollment_id=enrollment.id,
        milestone_id=milestone.id,
        status=ProgressStatus.PENDING,
    )
    db_session.add_all([program, cohort, enrollment, milestone, progress])
    await db_session.commit()

    original = app.dependency_overrides.get(get_current_user)
    try:
        app.dependency_overrides[get_current_user] = auth_override(
            make_member_user(user_id=str(uuid.uuid4()))
        )
        enrollment_response = await academy_client.get(
            f"/academy/enrollments/{enrollment.id}"
        )
        progress_response = await academy_client.get(
            f"/academy/enrollments/{enrollment.id}/progress"
        )
        assert enrollment_response.status_code == 403, enrollment_response.text
        assert progress_response.status_code == 403, progress_response.text

        app.dependency_overrides[get_current_user] = auth_override(
            make_member_user(user_id=owner.auth_id)
        )
        progress_response = await academy_client.get(
            f"/academy/enrollments/{enrollment.id}/progress"
        )
        assert progress_response.status_code == 200, progress_response.text
        assert any(item["id"] == str(progress.id) for item in progress_response.json())
    finally:
        if original is not None:
            app.dependency_overrides[get_current_user] = original
        else:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_cannot_claim_foreign_or_previously_achieved_milestone(
    academy_client, db_session, seed_member_row
):
    from services.academy_service.app.main import app
    from libs.common.datetime_utils import utc_now
    from services.academy_service.models import ProgressStatus, StudentProgress

    owner = await seed_member_row()
    program = ProgramFactory.create()
    foreign_program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=owner.id,
        member_auth_id=owner.auth_id,
    )
    own_milestone = MilestoneFactory.create(program_id=program.id)
    foreign_milestone = MilestoneFactory.create(program_id=foreign_program.id)
    achieved = StudentProgress(
        id=uuid.uuid4(),
        enrollment_id=enrollment.id,
        milestone_id=own_milestone.id,
        status=ProgressStatus.ACHIEVED,
        achieved_at=utc_now(),
    )
    db_session.add_all(
        [
            program,
            foreign_program,
            cohort,
            enrollment,
            own_milestone,
            foreign_milestone,
            achieved,
        ]
    )
    await db_session.commit()

    original = app.dependency_overrides.get(get_current_user)
    try:
        app.dependency_overrides[get_current_user] = auth_override(
            make_member_user(user_id=owner.auth_id)
        )
        foreign = await academy_client.post(
            f"/academy/enrollments/{enrollment.id}/progress/{foreign_milestone.id}/claim",
            json={},
        )
        assert foreign.status_code == 400, foreign.text

        already_achieved = await academy_client.post(
            f"/academy/enrollments/{enrollment.id}/progress/{own_milestone.id}/claim",
            json={},
        )
        assert already_achieved.status_code == 409, already_achieved.text
        await db_session.refresh(achieved)
        assert achieved.status == ProgressStatus.ACHIEVED
        assert achieved.achieved_at is not None
    finally:
        if original is not None:
            app.dependency_overrides[get_current_user] = original
        else:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_required_video_milestone_cannot_be_awarded_by_generic_progress_route(
    academy_client, db_session, seed_member_row
):
    from services.academy_service.models import RequiredEvidence, StudentProgress
    from sqlalchemy import select

    owner = await seed_member_row()
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    enrollment = EnrollmentFactory.create(
        program_id=program.id,
        cohort_id=cohort.id,
        member_id=owner.id,
        member_auth_id=owner.auth_id,
    )
    milestone = MilestoneFactory.create(
        program_id=program.id,
        required_evidence=RequiredEvidence.VIDEO,
    )
    db_session.add_all([program, cohort, enrollment, milestone])
    await db_session.commit()

    response = await academy_client.post(
        f"/academy/progress?enrollment_id={enrollment.id}&milestone_id={milestone.id}",
        json={"status": "achieved"},
    )
    assert response.status_code == 409, response.text
    existing = await db_session.scalar(
        select(StudentProgress).where(
            StudentProgress.enrollment_id == enrollment.id,
            StudentProgress.milestone_id == milestone.id,
        )
    )
    assert existing is None
