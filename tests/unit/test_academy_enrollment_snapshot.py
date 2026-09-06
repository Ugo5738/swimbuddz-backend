"""Commercial snapshots are captured by both enrollment creation endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.academy_service.models import CohortStatus
from services.academy_service.routers.enrollments import admin_crud, self_enroll
from services.academy_service.schemas import EnrollmentCreate


def result(value):
    return SimpleNamespace(
        scalar_one_or_none=lambda: value,
        scalar_one=lambda: value,
        scalar=lambda: value,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("creator", ["self", "admin"])
@pytest.mark.parametrize(
    "with_cohort,program_policy,override,expected",
    [
        (True, "active_required", "included", "included"),
        (True, "included", "open", "open"),
        (True, "included", None, "included"),
        (False, "active_required", None, "active_required"),
        (False, None, None, "open"),
    ],
)
async def test_creation_snapshots_server_commercial_terms(
    monkeypatch, creator, with_cohort, program_policy, override, expected
):
    program = SimpleNamespace(
        id=uuid4(),
        name="Learn to swim",
        is_published=True,
        price_amount=24_000_000,
        currency="NGN",
        membership_policy=program_policy,
    )
    cohort = (
        SimpleNamespace(
            id=uuid4(),
            name="Q4",
            program_id=program.id,
            status=CohortStatus.OPEN,
            capacity=12,
            price_override=22_000_000,
            membership_policy_override=override,
            coach_id=None,
        )
        if with_cohort
        else None
    )
    member_id = uuid4()
    added = []
    db = SimpleNamespace(
        add=Mock(side_effect=added.append),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    # Duplicate check, program/cohort lookup, capacity, then hydrated enrollment.
    rows = (
        [result(cohort), result(program), result(None), result(0)]
        if creator == "self" and cohort
        else [result(program), result(None)]
        if creator == "self"
        else [result(None), result(program)]
        + ([result(cohort), result(0)] if cohort else [])
    )
    rows.append(SimpleNamespace(scalar_one=lambda: added[0]))
    db.execute = AsyncMock(side_effect=rows)
    module = self_enroll if creator == "self" else admin_crud
    monkeypatch.setattr(module, "_sync_installment_state_for_enrollment", AsyncMock())
    monkeypatch.setattr(module, "dispatch_notification", AsyncMock())
    user = SimpleNamespace(user_id="member-auth")
    if creator == "self":
        monkeypatch.setattr(
            module, "get_member_by_auth_id", AsyncMock(return_value={"id": member_id})
        )
        enrollment = await module.self_enroll(
            {
                "program_id": str(program.id),
                "cohort_id": str(cohort.id) if cohort else None,
                # Clients cannot supply commercial snapshots.
                "membership_policy_snapshot": "untrusted",
            },
            current_user=user,
            db=db,
        )
    else:
        monkeypatch.setattr(module, "ensure_cohort_channel", AsyncMock())
        monkeypatch.setattr(module, "reconcile_cohort_membership", AsyncMock())
        enrollment = await module.enroll_student(
            EnrollmentCreate(
                program_id=program.id,
                cohort_id=cohort.id if cohort else None,
                member_id=member_id,
            ),
            current_user=user,
            db=db,
        )

    assert enrollment.membership_policy_snapshot == expected
    assert enrollment.price_snapshot_amount == (22_000_000 if cohort else 24_000_000)
    assert enrollment.currency_snapshot == "NGN"
    program.membership_policy = (
        "active_required" if expected == "included" else "included"
    )
    program.price_amount = 30_000_000
    program.currency = "USD"
    if cohort:
        cohort.membership_policy_override = program.membership_policy
        cohort.price_override = 30_000_000
    assert enrollment.membership_policy_snapshot == expected
    assert enrollment.price_snapshot_amount == (22_000_000 if cohort else 24_000_000)
    assert enrollment.currency_snapshot == "NGN"
