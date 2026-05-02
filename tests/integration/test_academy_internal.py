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
# GET /internal/academy/coaches/{coach_member_id}/cohort-ids
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
        f"/internal/academy/coaches/{member.id}/cohort-ids"
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
        f"/internal/academy/coaches/{member.id}/cohort-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(cohort.id) in [str(x) for x in data]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohort_ids_for_coach_empty(academy_client):
    """Returns empty list when coach has no cohorts."""
    response = await academy_client.get(
        f"/internal/academy/coaches/{uuid.uuid4()}/cohort-ids"
    )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /internal/academy/enrollments/{enrollment_id}
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
        f"/internal/academy/enrollments/{enrollment.id}"
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
    response = await academy_client.get(f"/internal/academy/enrollments/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /internal/academy/cohorts/{cohort_id}
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

    response = await academy_client.get(f"/internal/academy/cohorts/{cohort.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(cohort.id)
    assert data["name"] == cohort.name
    assert data["program_id"] == str(program.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_internal_not_found(academy_client):
    """Returns 404 for non-existent cohort."""
    response = await academy_client.get(f"/internal/academy/cohorts/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /internal/academy/cohorts/{cohort_id}/enrolled-students
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
        f"/internal/academy/cohorts/{cohort.id}/enrolled-students"
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
        f"/internal/academy/cohorts/{cohort.id}/enrolled-students"
    )

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /internal/academy/cohorts?status=open,active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohorts_internal_filters_by_status(academy_client, db_session):
    """Returns only cohorts whose status is in the comma-separated filter."""
    program = ProgramFactory.create(name="Adult Beginners")
    db_session.add(program)
    await db_session.flush()

    open_cohort = CohortFactory.create(program_id=program.id, status="OPEN")
    active_cohort = CohortFactory.create(program_id=program.id, status="ACTIVE")
    completed_cohort = CohortFactory.create(program_id=program.id, status="COMPLETED")
    db_session.add_all([open_cohort, active_cohort, completed_cohort])
    await db_session.commit()

    response = await academy_client.get(
        "/internal/academy/cohorts", params={"status": "open,active"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "cohorts" in body
    returned_ids = {c["id"] for c in body["cohorts"]}
    assert str(open_cohort.id) in returned_ids
    assert str(active_cohort.id) in returned_ids
    assert str(completed_cohort.id) not in returned_ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohorts_internal_response_shape(academy_client, db_session):
    """Each cohort dict has the keys expected by reporting_service flywheel."""
    program = ProgramFactory.create(name="Stroke Refinement")
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id, status="OPEN")
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(
        "/internal/academy/cohorts", params={"status": "open"}
    )

    assert response.status_code == 200
    cohorts = response.json()["cohorts"]
    assert len(cohorts) >= 1
    item = next(c for c in cohorts if c["id"] == str(cohort.id))
    assert set(item.keys()) >= {
        "id",
        "name",
        "program_name",
        "capacity",
        "status",
        "start_date",
        "end_date",
    }
    assert item["program_name"] == "Stroke Refinement"
    assert item["status"] == "open"
    assert isinstance(item["capacity"], int)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohorts_internal_invalid_status(academy_client):
    """Unknown status values return 400."""
    response = await academy_client.get(
        "/internal/academy/cohorts", params={"status": "open,bogus"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohorts_internal_missing_status(academy_client):
    """Missing status query param returns 422 (FastAPI validation)."""
    response = await academy_client.get("/internal/academy/cohorts")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /internal/academy/cohorts/{cohort_id}/enrollment-counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_enrollment_counts_groups_by_status(academy_client, db_session):
    """Counts are grouped by EnrollmentStatus and mapped to lowercase keys."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    enrollments = [
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="ENROLLED"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="ENROLLED"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="PENDING_APPROVAL"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="WAITLIST"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="DROPPED"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="GRADUATED"
        ),
    ]
    db_session.add_all(enrollments)
    await db_session.commit()

    response = await academy_client.get(
        f"/internal/academy/cohorts/{cohort.id}/enrollment-counts"
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "active": 2,
        "pending_approval": 1,
        "waitlist": 1,
        "dropped": 1,
        "graduated": 1,
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_enrollment_counts_dropout_pending_folds_into_dropped(
    academy_client, db_session
):
    """DROPOUT_PENDING enrollments are counted under 'dropped'."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    enrollments = [
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="DROPPED"
        ),
        EnrollmentFactory.create(
            cohort_id=cohort.id, program_id=program.id, status="DROPOUT_PENDING"
        ),
    ]
    db_session.add_all(enrollments)
    await db_session.commit()

    response = await academy_client.get(
        f"/internal/academy/cohorts/{cohort.id}/enrollment-counts"
    )
    assert response.status_code == 200
    assert response.json()["dropped"] == 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_enrollment_counts_empty_cohort(academy_client, db_session):
    """A cohort with no enrollments returns all zeros (no 404)."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.commit()

    response = await academy_client.get(
        f"/internal/academy/cohorts/{cohort.id}/enrollment-counts"
    )
    assert response.status_code == 200
    assert response.json() == {
        "active": 0,
        "pending_approval": 0,
        "waitlist": 0,
        "dropped": 0,
        "graduated": 0,
    }
