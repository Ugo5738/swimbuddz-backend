"""Contract tests for academy_service internal endpoints.

These tests verify response shapes match what other services expect.
"""


import pytest
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MemberFactory,
    ProgramFactory,
)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_cohort_internal_contract(academy_client, db_session):
    """Communications service depends on this shape for cohort validation."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(f"/academy/internal/cohorts/{cohort.id}")
    assert response.status_code == 200
    data = response.json()

    required_fields = ["id", "name", "coach_id", "program_id", "status"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in cohort internal response. "
            f"Used by communications_service."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_enrolled_students_contract(academy_client, db_session):
    """Communications service depends on this shape for messaging."""
    member = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, program])
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id,
        member_id=member.id,
        program_id=program.id,
    )
    db_session.add(enrollment)
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/cohorts/{cohort.id}/enrolled-students"
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1
    required_fields = ["enrollment_id", "member_id", "status"]
    for field in required_fields:
        assert field in data[0], (
            f"Missing contract field '{field}' in enrolled-students response. "
            f"Used by communications_service."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_enrollment_internal_contract(academy_client, db_session):
    """Payments service depends on this shape for enrollment lookup."""
    member = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, program])
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id,
        member_id=member.id,
        program_id=program.id,
    )
    db_session.add(enrollment)
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/enrollments/{enrollment.id}"
    )
    assert response.status_code == 200
    data = response.json()

    required_fields = ["id", "member_id", "status", "payment_status"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in enrollment response. "
            f"Used by payments_service."
        )
