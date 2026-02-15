"""Integration tests for academy_service internal endpoints."""

import uuid

import pytest
from tests.factories import (
    CoachAssignmentFactory,
    CohortFactory,
    EnrollmentFactory,
    MemberFactory,
    ProgramFactory,
)

# ---------------------------------------------------------------------------
# GET /academy/internal/coaches/{coach_member_id}/cohort-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohort_ids_for_coach_legacy(academy_client, db_session):
    """Returns cohort IDs where coach is assigned via legacy coach_id field."""
    member = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, program])
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id, coach_id=member.id)
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/coaches/{member.id}/cohort-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(cohort.id) in [str(x) for x in data]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohort_ids_for_coach_assignment(academy_client, db_session):
    """Returns cohort IDs from active CoachAssignment records."""
    member = MemberFactory.create()
    admin = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, admin, program])
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    assignment = CoachAssignmentFactory.create(
        cohort_id=cohort.id,
        coach_id=member.id,
        assigned_by_id=admin.id,
        role="lead",
        status="active",
    )
    db_session.add(assignment)
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/coaches/{member.id}/cohort-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(cohort.id) in [str(x) for x in data]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohort_ids_for_coach_empty(academy_client):
    """Returns empty list when coach has no cohorts."""
    response = await academy_client.get(
        f"/academy/internal/coaches/{uuid.uuid4()}/cohort-ids"
    )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /academy/internal/enrollments/{enrollment_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_enrollment_internal(academy_client, db_session):
    """Returns enrollment with cohort and program details."""
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
    assert data["id"] == str(enrollment.id)
    assert data["member_id"] == str(member.id)
    assert data["status"] == "enrolled"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_enrollment_internal_not_found(academy_client):
    """Returns 404 for non-existent enrollment."""
    response = await academy_client.get(f"/academy/internal/enrollments/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /academy/internal/cohorts/{cohort_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_internal(academy_client, db_session):
    """Returns basic cohort info dict."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(f"/academy/internal/cohorts/{cohort.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(cohort.id)
    assert data["name"] == cohort.name
    assert data["program_id"] == str(program.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_internal_not_found(academy_client):
    """Returns 404 for non-existent cohort."""
    response = await academy_client.get(f"/academy/internal/cohorts/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /academy/internal/cohorts/{cohort_id}/enrolled-students
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_enrolled_students(academy_client, db_session):
    """Returns enrolled members for a cohort."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([m1, m2, program])
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    e1 = EnrollmentFactory.create(
        cohort_id=cohort.id,
        member_id=m1.id,
        program_id=program.id,
    )
    # Dropped student should NOT appear
    e2 = EnrollmentFactory.create(
        cohort_id=cohort.id,
        member_id=m2.id,
        program_id=program.id,
        status="DROPPED",
    )
    db_session.add_all([e1, e2])
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/cohorts/{cohort.id}/enrolled-students"
    )

    assert response.status_code == 200
    data = response.json()
    member_ids = [item["member_id"] for item in data]
    assert str(m1.id) in member_ids
    assert str(m2.id) not in member_ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_enrolled_students_empty(academy_client, db_session):
    """Returns empty list for cohort with no enrollments."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(
        f"/academy/internal/cohorts/{cohort.id}/enrolled-students"
    )

    assert response.status_code == 200
    assert response.json() == []
