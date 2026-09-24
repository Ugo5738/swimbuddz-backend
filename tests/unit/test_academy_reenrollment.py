"""Historical cohorts must not block a member's next Academy enrollment."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.academy_service.models import (
    CohortStatus,
    EnrollmentStatus,
    PaymentStatus,
)
from services.academy_service.routers.enrollments import self_enroll


def result(value):
    return SimpleNamespace(
        scalar_one_or_none=lambda: (value[0] if value else None)
        if isinstance(value, list)
        else value,
        scalar_one=lambda: value,
        scalar=lambda: value,
        scalars=lambda: SimpleNamespace(all=lambda: value),
    )


@pytest.fixture
def enrollment_case(monkeypatch):
    program = SimpleNamespace(
        id=uuid4(),
        name="Beginner Freestyle",
        is_published=True,
        price_amount=20_000_000,
        currency="NGN",
        membership_policy="open",
    )
    cohort = SimpleNamespace(
        id=uuid4(),
        program_id=program.id,
        name="September",
        status=CohortStatus.OPEN,
        capacity=10,
        price_override=None,
        membership_policy_override=None,
    )
    member_id = uuid4()
    added = []
    db = SimpleNamespace(
        add=Mock(side_effect=added.append),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        self_enroll, "get_member_by_auth_id", AsyncMock(return_value={"id": member_id})
    )
    monkeypatch.setattr(
        self_enroll, "_sync_installment_state_for_enrollment", AsyncMock()
    )
    monkeypatch.setattr(self_enroll, "dispatch_notification", AsyncMock())

    def previous(status, enrollment_status=EnrollmentStatus.ENROLLED, *, same=False):
        prior_cohort = (
            SimpleNamespace(
                id=cohort.id if same else uuid4(), name="February", status=status
            )
            if status is not None
            else None
        )
        return SimpleNamespace(
            id=uuid4(),
            member_id=member_id,
            program_id=program.id,
            cohort_id=prior_cohort.id if prior_cohort else None,
            cohort=prior_cohort,
            status=enrollment_status,
            payment_status=PaymentStatus.PAID,
        )

    async def enroll(existing, capacity_used=1):
        db.execute = AsyncMock(
            side_effect=[
                result(cohort),
                result(program),
                result(existing),
                result(capacity_used),
                SimpleNamespace(scalar_one=lambda: added[0]),
            ]
        )
        return await self_enroll.self_enroll(
            {"cohort_id": str(cohort.id)},
            current_user=SimpleNamespace(user_id="member-auth"),
            db=db,
        )

    return SimpleNamespace(
        cohort=cohort, db=db, added=added, previous=previous, enroll=enroll
    )


@pytest.mark.parametrize("status", [CohortStatus.COMPLETED, CohortStatus.CANCELLED])
async def test_terminal_cohort_does_not_block_new_enrollment(enrollment_case, status):
    case = enrollment_case
    old = case.previous(status)
    enrollment = await case.enroll([old])
    assert enrollment.cohort_id == case.cohort.id
    assert enrollment.status == EnrollmentStatus.PENDING_APPROVAL
    assert enrollment.payment_status == PaymentStatus.PENDING
    assert enrollment.price_snapshot_amount == 20_000_000
    # Enrollment history and settled payments remain untouched.
    assert old.status == EnrollmentStatus.ENROLLED
    assert old.payment_status == PaymentStatus.PAID
    case.db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "capacity_used,expected",
    [
        (0, EnrollmentStatus.PENDING_APPROVAL),
        (10, EnrollmentStatus.WAITLIST),
    ],
)
async def test_first_time_member_follows_capacity_rules(
    enrollment_case, capacity_used, expected
):
    case = enrollment_case
    enrollment = await case.enroll([], capacity_used)
    assert enrollment.status == expected
    assert enrollment.payment_status == PaymentStatus.PENDING


@pytest.mark.parametrize(
    "cohort_status", [CohortStatus.OPEN, CohortStatus.ACTIVE, None]
)
@pytest.mark.parametrize(
    "enrollment_status",
    [
        EnrollmentStatus.ENROLLED,
        EnrollmentStatus.PENDING_APPROVAL,
        EnrollmentStatus.WAITLIST,
    ],
)
async def test_live_or_unassigned_enrollment_still_blocks_duplicates(
    enrollment_case,
    cohort_status,
    enrollment_status,
):
    case = enrollment_case
    existing = case.previous(cohort_status, enrollment_status)
    with pytest.raises(HTTPException) as error:
        await case.enroll([case.previous(CohortStatus.COMPLETED), existing])
    assert error.value.status_code == 400
    assert "already have an active enrollment" in error.value.detail
    case.db.add.assert_not_called()
    case.db.commit.assert_not_awaited()


async def test_same_cohort_still_blocks_duplicate_enrollment(enrollment_case):
    case = enrollment_case
    with pytest.raises(HTTPException) as error:
        await case.enroll([case.previous(CohortStatus.OPEN, same=True)])
    assert error.value.detail == "You are already enrolled in this cohort"
    case.db.add.assert_not_called()
