"""Integration test for the internal make-up obligation schedule endpoint (Phase 1).

sessions_service calls this (service-role) to flip a cohort obligation to
SCHEDULED when an admin confirms a make-up. ``payments_client`` is wired with
the service-role override in conftest.
"""

import uuid

import pytest
from sqlalchemy import select

from services.payments_service.models import (
    CohortMakeupObligation,
    MakeupReason,
    MakeupStatus,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_internal_schedule_flips_obligation(payments_client, db_session):
    obligation = CohortMakeupObligation(
        cohort_id=uuid.uuid4(),
        student_member_id=uuid.uuid4(),
        coach_member_id=uuid.uuid4(),
        reason=MakeupReason.EXCUSED_ABSENCE,
        status=MakeupStatus.PENDING,
    )
    db_session.add(obligation)
    await db_session.commit()
    session_id = uuid.uuid4()

    r = await payments_client.post(
        f"/internal/payments/makeup-obligations/{obligation.id}/schedule",
        json={"scheduled_session_id": str(session_id), "notes": "make-up booked"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"

    refreshed = (
        await db_session.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation.id
            )
        )
    ).scalar_one()
    assert refreshed.status == MakeupStatus.SCHEDULED
    assert str(refreshed.scheduled_session_id) == str(session_id)


async def test_internal_schedule_missing_obligation_404(payments_client):
    r = await payments_client.post(
        f"/internal/payments/makeup-obligations/{uuid.uuid4()}/schedule",
        json={"scheduled_session_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_internal_complete_flips_obligation(payments_client, db_session):
    obligation = CohortMakeupObligation(
        cohort_id=uuid.uuid4(),
        student_member_id=uuid.uuid4(),
        coach_member_id=uuid.uuid4(),
        reason=MakeupReason.EXCUSED_ABSENCE,
        status=MakeupStatus.SCHEDULED,
    )
    db_session.add(obligation)
    await db_session.commit()

    r = await payments_client.post(
        f"/internal/payments/makeup-obligations/{obligation.id}/complete"
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    refreshed = (
        await db_session.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation.id
            )
        )
    ).scalar_one()
    assert refreshed.status == MakeupStatus.COMPLETED
    assert refreshed.completed_at is not None
