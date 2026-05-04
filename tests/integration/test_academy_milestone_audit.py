"""Integration tests for the milestone review audit trail.

Verifies that every claim, review, and status change on a StudentProgress row
emits a row in ``milestone_review_events`` with correct snapshots, and that the
history endpoint respects authorization.
"""

import uuid

import pytest
from sqlalchemy import select

from tests.conftest import make_coach_user, make_member_user
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MilestoneFactory,
    ProgramFactory,
)


async def _seed_enrollment_and_milestone(db_session, *, member_auth_id=None):
    """Create program + cohort + enrollment + milestone ready for claims."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    member_auth_id = member_auth_id or str(uuid.uuid4())
    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id,
        program_id=program.id,
        member_auth_id=member_auth_id,
    )
    milestone = MilestoneFactory.create(program_id=program.id)
    db_session.add_all([enrollment, milestone])
    await db_session.commit()

    return enrollment, milestone, cohort


def _override_user(app, user):
    """Replace get_current_user on the app with one returning ``user``."""
    from libs.auth.dependencies import get_current_user

    async def _fn():
        return user

    app.dependency_overrides[get_current_user] = _fn


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_writes_event(academy_client, db_session):
    """A student claim emits exactly one 'claimed' event with snapshots."""
    from services.academy_service.app.main import app as academy_app
    from services.academy_service.models import MilestoneReviewEvent

    auth_id = str(uuid.uuid4())
    enrollment, milestone, _ = await _seed_enrollment_and_milestone(
        db_session, member_auth_id=auth_id
    )

    _override_user(academy_app, make_member_user(user_id=auth_id))

    media_id = str(uuid.uuid4())
    response = await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"evidence_media_id": media_id, "student_notes": "attempt 1"},
    )
    assert response.status_code == 200, response.text

    result = await db_session.execute(
        select(MilestoneReviewEvent).where(
            MilestoneReviewEvent.enrollment_id == enrollment.id
        )
    )
    events = result.scalars().all()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type.value == "claimed"
    assert ev.actor_role == "student"
    assert ev.student_notes_snapshot == "attempt 1"
    assert str(ev.evidence_media_id_snapshot) == media_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reject_then_resubmit_preserves_history(academy_client, db_session):
    """After reject → resubmit, both events (with rejection feedback) survive."""
    from services.academy_service.app.main import app as academy_app
    from services.academy_service.models import MilestoneReviewEvent, StudentProgress

    auth_id = str(uuid.uuid4())
    enrollment, milestone, _ = await _seed_enrollment_and_milestone(
        db_session, member_auth_id=auth_id
    )

    # 1. Student claims
    _override_user(academy_app, make_member_user(user_id=auth_id))
    r = await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"student_notes": "attempt 1"},
    )
    assert r.status_code == 200

    # 2. Coach rejects via /progress (status back to pending + coach_notes)
    _override_user(academy_app, make_coach_user(user_id=str(uuid.uuid4())))
    r = await academy_client.post(
        "/academy/progress",
        params={
            "enrollment_id": str(enrollment.id),
            "milestone_id": str(milestone.id),
        },
        json={
            "status": "pending",
            "coach_notes": "kick technique off — please redo",
        },
    )
    assert r.status_code == 200, r.text

    # 3. Student resubmits — the current row's coach_notes is nulled
    _override_user(academy_app, make_member_user(user_id=auth_id))
    r = await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"student_notes": "attempt 2"},
    )
    assert r.status_code == 200

    # The live progress row has lost the coach_notes, but history preserves it
    progress = (
        await db_session.execute(
            select(StudentProgress).where(
                StudentProgress.enrollment_id == enrollment.id
            )
        )
    ).scalar_one()
    assert progress.coach_notes is None

    events = (
        (
            await db_session.execute(
                select(MilestoneReviewEvent)
                .where(MilestoneReviewEvent.progress_id == progress.id)
                .order_by(MilestoneReviewEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert [e.event_type.value for e in events] == [
        "claimed",
        "rejected",
        "claimed",
    ]
    assert events[1].coach_notes_snapshot == "kick technique off — please redo"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_evidence_snapshot_per_attempt(academy_client, db_session):
    """Each claim snapshots the evidence_media_id it was submitted with."""
    from services.academy_service.app.main import app as academy_app
    from services.academy_service.models import MilestoneReviewEvent

    auth_id = str(uuid.uuid4())
    enrollment, milestone, _ = await _seed_enrollment_and_milestone(
        db_session, member_auth_id=auth_id
    )

    media_a, media_b = str(uuid.uuid4()), str(uuid.uuid4())
    _override_user(academy_app, make_member_user(user_id=auth_id))

    await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"evidence_media_id": media_a, "student_notes": "v1"},
    )
    # Coach rejects so student can resubmit
    _override_user(academy_app, make_coach_user(user_id=str(uuid.uuid4())))
    await academy_client.post(
        "/academy/progress",
        params={
            "enrollment_id": str(enrollment.id),
            "milestone_id": str(milestone.id),
        },
        json={"status": "pending", "coach_notes": "redo"},
    )
    _override_user(academy_app, make_member_user(user_id=auth_id))
    await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"evidence_media_id": media_b, "student_notes": "v2"},
    )

    events = (
        (
            await db_session.execute(
                select(MilestoneReviewEvent)
                .where(MilestoneReviewEvent.enrollment_id == enrollment.id)
                .where(MilestoneReviewEvent.event_type == "claimed")
                .order_by(MilestoneReviewEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert str(events[0].evidence_media_id_snapshot) == media_a
    assert str(events[1].evidence_media_id_snapshot) == media_b


@pytest.mark.asyncio
@pytest.mark.integration
async def test_history_endpoint_returns_events_in_order(academy_client, db_session):
    """The GET /events endpoint returns a chronologically ordered list."""
    from services.academy_service.app.main import app as academy_app
    from services.academy_service.models import StudentProgress

    auth_id = str(uuid.uuid4())
    enrollment, milestone, _ = await _seed_enrollment_and_milestone(
        db_session, member_auth_id=auth_id
    )

    # Claim → reject → claim → approve
    _override_user(academy_app, make_member_user(user_id=auth_id))
    await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"student_notes": "attempt 1"},
    )
    _override_user(academy_app, make_coach_user(user_id=str(uuid.uuid4())))
    await academy_client.post(
        "/academy/progress",
        params={
            "enrollment_id": str(enrollment.id),
            "milestone_id": str(milestone.id),
        },
        json={"status": "pending", "coach_notes": "too fast"},
    )
    _override_user(academy_app, make_member_user(user_id=auth_id))
    await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"student_notes": "attempt 2"},
    )
    _override_user(academy_app, make_coach_user(user_id=str(uuid.uuid4())))
    await academy_client.post(
        "/academy/progress",
        params={
            "enrollment_id": str(enrollment.id),
            "milestone_id": str(milestone.id),
        },
        json={"status": "achieved", "coach_notes": "great"},
    )

    progress = (
        await db_session.execute(
            select(StudentProgress).where(
                StudentProgress.enrollment_id == enrollment.id
            )
        )
    ).scalar_one()

    # GET events as the owning student — avoids require_coach_for_cohort's
    # outbound HTTP call to the members service.
    _override_user(academy_app, make_member_user(user_id=auth_id))
    response = await academy_client.get(
        f"/academy/enrollments/{enrollment.id}/progress/{progress.id}/events"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["event_type"] for e in body] == [
        "claimed",
        "rejected",
        "claimed",
        "approved",
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_history_endpoint_authz_rejects_unrelated_member(
    academy_client, db_session
):
    """A non-owner, non-coach member gets 403 on the history endpoint."""
    from services.academy_service.app.main import app as academy_app
    from services.academy_service.models import StudentProgress

    owner_auth = str(uuid.uuid4())
    enrollment, milestone, _ = await _seed_enrollment_and_milestone(
        db_session, member_auth_id=owner_auth
    )

    _override_user(academy_app, make_member_user(user_id=owner_auth))
    await academy_client.post(
        f"/academy/enrollments/{enrollment.id}/progress/{milestone.id}/claim",
        json={"student_notes": "done"},
    )
    progress = (
        await db_session.execute(
            select(StudentProgress).where(
                StudentProgress.enrollment_id == enrollment.id
            )
        )
    ).scalar_one()

    # Impersonate a stranger (not owner, not coach, not admin). The endpoint
    # reads current_user via get_current_user and then gates access via
    # require_coach_for_cohort, which will 403 for a non-assigned coach.
    stranger = make_member_user(user_id=str(uuid.uuid4()))
    _override_user(academy_app, stranger)

    response = await academy_client.get(
        f"/academy/enrollments/{enrollment.id}/progress/{progress.id}/events"
    )
    assert response.status_code == 403
