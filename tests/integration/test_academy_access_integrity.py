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
        async def unrelated_member():
            return make_member_user(user_id=str(uuid.uuid4()))

        app.dependency_overrides[get_current_user] = unrelated_member
        enrollment_response = await academy_client.get(
            f"/academy/enrollments/{enrollment.id}"
        )
        progress_response = await academy_client.get(
            f"/academy/enrollments/{enrollment.id}/progress"
        )
        assert enrollment_response.status_code == 403, enrollment_response.text
        assert progress_response.status_code == 403, progress_response.text

        async def actual_owner():
            return make_member_user(user_id=owner.auth_id)

        app.dependency_overrides[get_current_user] = actual_owner
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
        async def actual_owner():
            return make_member_user(user_id=owner.auth_id)

        app.dependency_overrides[get_current_user] = actual_owner
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
