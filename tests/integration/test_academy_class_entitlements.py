"""PostgreSQL checks for one class with distinct learner financial treatment."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    AcademyClassAllocation,
    AcademyClassPlan,
    EnrollmentStatus,
)
from services.academy_service.routers.enrollments import class_entitlements as service
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MemberFactory,
    ProgramFactory,
)
from sqlalchemy import select

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class SessionResponse:
    status_code = 200

    def __init__(self, session_id, cohort_id):
        self.session_id = session_id
        self.cohort_id = cohort_id

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "id": str(self.session_id),
            "cohort_id": str(self.cohort_id),
            "session_type": "cohort_class",
            "starts_at": utc_now().isoformat(),
        }


async def test_same_session_separates_prepaid_and_paid_extra(db_session, monkeypatch):
    program = ProgramFactory.create(duration_weeks=12)
    cohort = CohortFactory.create(program_id=program.id)
    member_a = MemberFactory.create()
    member_b = MemberFactory.create()
    db_session.add_all([program, cohort, member_a, member_b])
    await db_session.flush()
    enrollment_a = EnrollmentFactory.create(
        member_id=member_a.id,
        cohort_id=cohort.id,
        program_id=program.id,
        status=EnrollmentStatus.ENROLLED,
    )
    enrollment_b = EnrollmentFactory.create(
        member_id=member_b.id,
        cohort_id=cohort.id,
        program_id=program.id,
        status=EnrollmentStatus.ENROLLED,
    )
    db_session.add_all([enrollment_a, enrollment_b])
    await db_session.commit()
    session_id = uuid.uuid4()
    monkeypatch.setattr(
        service,
        "internal_get",
        AsyncMock(return_value=SessionResponse(session_id, cohort.id)),
    )
    actor = AuthUser(user_id="admin")
    for enrollment, count in ((enrollment_a, 3), (enrollment_b, 5)):
        await service.configure_class_baseline(
            enrollment.id,
            service.ClassBaselineInput(
                purchased_classes=12,
                previously_consumed=count,
                opening_as_of=utc_now() - timedelta(hours=1),
                reason="Verified attendance history before class",
            ),
            actor,
            db_session,
        )
    prepaid = service.ClassAllocationInput(
        session_id=session_id,
        member_id=member_a.id,
        actor_auth_id="admin",
        kind="prepaid",
        agreed_fee_kobo=0,
        reason="Two weekly lessons from existing tuition",
    )
    extra = service.ClassAllocationInput(
        session_id=session_id,
        member_id=member_b.id,
        actor_auth_id="admin",
        kind="paid_extra",
        agreed_fee_kobo=1_500_000,
        reason="Additional Saturday session, separately agreed",
    )
    first = await service.reserve_class_allocation(
        enrollment_a.id, prepaid, actor, db_session
    )
    second = await service.reserve_class_allocation(
        enrollment_b.id, extra, actor, db_session
    )
    assert first["status"] == second["status"] == "reserved"
    replay = await service.reserve_class_allocation(
        enrollment_a.id, prepaid, actor, db_session
    )
    assert replay["idempotent"]
    assert replay["id"] == first["id"]
    await service.apply_class_allocation(
        enrollment_a.id,
        uuid.UUID(first["id"]),
        service.ClassAllocationApply(booking_id=uuid.uuid4()),
        actor,
        db_session,
    )
    await service.apply_class_allocation(
        enrollment_b.id,
        uuid.UUID(second["id"]),
        service.ClassAllocationApply(booking_id=uuid.uuid4()),
        actor,
        db_session,
    )
    a = await service.class_entitlements(enrollment_a.id, actor, db_session)
    b = await service.class_entitlements(enrollment_b.id, actor, db_session)
    assert a["remaining"] == 8
    assert b["remaining"] == 7
    assert a["applied_prepaid"] == 1
    assert b["applied_prepaid"] == 0
    rows = (
        (
            await db_session.execute(
                select(AcademyClassAllocation).where(
                    AcademyClassAllocation.session_id == session_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {row.kind for row in rows} == {"prepaid", "paid_extra"}
    assert sorted(row.agreed_fee_kobo for row in rows) == [0, 1_500_000]
    assert (await db_session.execute(select(AcademyClassPlan))).scalars().all()
