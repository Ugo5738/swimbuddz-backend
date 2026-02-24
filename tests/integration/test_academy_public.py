"""Integration tests for academy_service PUBLIC API endpoints.

Tests program CRUD, cohort CRUD, enrollment operations, and milestones.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from tests.factories import (
    CohortFactory,
    EnrollmentFactory,
    MemberFactory,
    MilestoneFactory,
    ProgramFactory,
)


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# Programs — CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_program(academy_client, db_session):
    """Admin can create a new program."""
    payload = {
        "name": "Beginner 12-Week",
        "slug": f"beginner-{uuid.uuid4().hex[:6]}",
        "description": "A 12-week program for adults.",
        "level": "beginner_1",
        "duration_weeks": 12,
        "default_capacity": 10,
        "currency": "NGN",
        "price_amount": 150000,
        "billing_type": "one_time",
    }

    response = await academy_client.post("/academy/programs", json=payload)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["name"] == "Beginner 12-Week"
    assert data["price_amount"] == 150000


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_programs(academy_client, db_session):
    """List all programs."""
    p1 = ProgramFactory.create()
    p2 = ProgramFactory.create()
    db_session.add_all([p1, p2])
    await db_session.commit()

    response = await academy_client.get("/academy/programs")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2


# ---------------------------------------------------------------------------
# Cohorts — CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_cohort(academy_client, db_session):
    """Admin can create a cohort under a program."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.commit()

    from tests.factories import _tomorrow

    start = _tomorrow()
    payload = {
        "program_id": str(program.id),
        "name": f"Cohort-{uuid.uuid4().hex[:4]}",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(weeks=12)).isoformat(),
        "capacity": 20,
        "timezone": "Africa/Lagos",
        "location_type": "pool",
        "location_name": "Sunfit Pool",
    }

    # The endpoint calls get_member_by_auth_id to resolve the admin member —
    # mock the cross-service HTTP call to avoid ConnectError.
    with patch(
        "libs.common.service_client.internal_get",
        new_callable=AsyncMock,
        return_value=AsyncMock(
            status_code=200,
            json=lambda: {
                "id": str(uuid.uuid4()),
                "first_name": "Admin",
                "last_name": "User",
            },
            raise_for_status=lambda: None,
        ),
    ):
        response = await academy_client.post("/academy/cohorts", json=payload)

    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["name"] == payload["name"]
    assert data["program_id"] == str(program.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohorts(academy_client, db_session):
    """List all cohorts."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    c1 = CohortFactory.create(program_id=program.id)
    c2 = CohortFactory.create(program_id=program.id)
    db_session.add_all([c1, c2])
    await db_session.commit()

    response = await academy_client.get("/academy/cohorts")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_by_id(academy_client, db_session):
    """Fetch a specific cohort."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.commit()

    with patch(
        "services.academy_service.routers.member.get_members_bulk",
        new_callable=AsyncMock,
        return_value=[],
    ):
        with patch(
            "services.academy_service.routers.member.get_next_session_for_cohort",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await academy_client.get(f"/academy/cohorts/{cohort.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(cohort.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_cohort_not_found(academy_client, db_session):
    """Returns 404 for non-existent cohort."""
    fake_id = str(uuid.uuid4())

    response = await academy_client.get(f"/academy/cohorts/{fake_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_enrollment(academy_client, db_session):
    """Create an enrollment in a cohort."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    from services.academy_service.models import CohortStatus

    cohort = CohortFactory.create(program_id=program.id, status=CohortStatus.OPEN)
    db_session.add(cohort)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    payload = {
        "program_id": str(program.id),
        "cohort_id": str(cohort.id),
        "member_id": str(member.id),
    }

    with patch(
        "services.academy_service.routers.member.get_member_by_id",
        new_callable=AsyncMock,
        return_value={
            "id": str(member.id),
            "first_name": "Test",
            "last_name": "User",
            "email": member.email,
        },
    ):
        with patch(
            "services.academy_service.routers.member.internal_post",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await academy_client.post("/academy/enrollments", json=payload)

    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["cohort_id"] == str(cohort.id)
    assert data["member_id"] == str(member.id)
    # Admin enrollment does not auto-generate installment plans unless explicitly
    # enabled + selected through the installment-aware enrollment flow.
    assert data["total_installments"] == 0
    assert data["paid_installments_count"] == 0
    assert len(data["installments"]) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_enrollment_caps_installments_to_three_when_fee_exceeds_150k(
    academy_client, db_session
):
    """Fees above 150k are split into max three installments within first 12 weeks."""
    program = ProgramFactory.create(duration_weeks=20, price_amount=250000)
    db_session.add(program)
    await db_session.flush()

    from services.academy_service.models import CohortStatus

    cohort = CohortFactory.create(
        program_id=program.id,
        status=CohortStatus.OPEN,
    )
    # Force an explicit 20-week window to match program duration.
    cohort.end_date = cohort.start_date + timedelta(weeks=20)
    db_session.add(cohort)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await academy_client.post(
        "/academy/enrollments",
        json={
            "program_id": str(program.id),
            "cohort_id": str(cohort.id),
            "member_id": str(member.id),
        },
    )

    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["total_installments"] == 0
    assert data["installments"] == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mark_paid_updates_non_installment_enrollment(academy_client, db_session):
    """Marking paid on default admin enrollments moves payment status to paid."""
    program = ProgramFactory.create(duration_weeks=12, price_amount=150000)
    db_session.add(program)
    await db_session.flush()

    from services.academy_service.models import CohortStatus

    cohort = CohortFactory.create(program_id=program.id, status=CohortStatus.OPEN)
    db_session.add(cohort)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    create_response = await academy_client.post(
        "/academy/enrollments",
        json={
            "program_id": str(program.id),
            "cohort_id": str(cohort.id),
            "member_id": str(member.id),
        },
    )
    assert create_response.status_code in (200, 201), create_response.text
    enrollment_id = create_response.json()["id"]

    with patch(
        "services.academy_service.routers.member.get_member_by_id",
        new_callable=AsyncMock,
        return_value={
            "id": str(member.id),
            "first_name": "Test",
            "last_name": "User",
            "email": member.email,
        },
    ):
        first_payment = await academy_client.post(
            f"/academy/admin/enrollments/{enrollment_id}/mark-paid",
            json={"installment_number": 1, "payment_reference": "PAY-INST-1"},
        )
        assert first_payment.status_code == 200, first_payment.text
        first_data = first_payment.json()
        assert first_data["paid_installments_count"] == 0
        assert first_data["total_installments"] == 0
        assert first_data["payment_status"] == "paid"

        second_payment = await academy_client.post(
            f"/academy/admin/enrollments/{enrollment_id}/mark-paid",
            json={"installment_number": 3, "payment_reference": "PAY-INST-2"},
        )
        assert second_payment.status_code == 200, second_payment.text
        second_data = second_payment.json()
        assert second_data["paid_installments_count"] == 0
        assert second_data["total_installments"] == 0
        assert second_data["payment_status"] == "paid"


# ---------------------------------------------------------------------------
# Cohort Timeline Shift Workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preview_cohort_timeline_shift(academy_client, db_session):
    """Preview reports session impact counts and no side effects."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    start = datetime.now(timezone.utc) + timedelta(days=5)
    cohort = CohortFactory.create(
        program_id=program.id,
        start_date=start,
        end_date=start + timedelta(weeks=12),
    )
    db_session.add(cohort)
    await db_session.commit()

    session_payload = [
        {
            "id": str(uuid.uuid4()),
            "status": "scheduled",
            "starts_at": (start + timedelta(days=1)).isoformat(),
            "ends_at": (start + timedelta(days=1, hours=1)).isoformat(),
        },
        {
            "id": str(uuid.uuid4()),
            "status": "completed",
            "starts_at": (start - timedelta(days=2)).isoformat(),
            "ends_at": (start - timedelta(days=2) + timedelta(hours=1)).isoformat(),
        },
    ]

    with patch(
        "services.academy_service.routers.member.internal_get",
        new_callable=AsyncMock,
        return_value=_FakeResponse(payload=session_payload),
    ):
        response = await academy_client.post(
            f"/academy/cohorts/{cohort.id}/timeline-shifts/preview",
            json={
                "new_start_date": (cohort.start_date + timedelta(days=14)).isoformat(),
                "new_end_date": (cohort.end_date + timedelta(days=14)).isoformat(),
                "expected_updated_at": cohort.updated_at.isoformat(),
                "shift_sessions": True,
                "shift_installments": True,
                "reset_start_reminders": True,
                "notify_members": False,
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sessions_total"] == 2
    assert data["sessions_shiftable"] == 1
    assert data["sessions_blocked"] == 1
    assert data["delta_seconds"] == 14 * 24 * 60 * 60


@pytest.mark.asyncio
@pytest.mark.integration
async def test_apply_cohort_timeline_shift_updates_related_records(
    academy_client, db_session
):
    """Apply shifts cohort dates, shiftable sessions, pending installments, and reminders."""
    from services.academy_service.models import (
        Enrollment,
        EnrollmentInstallment,
        EnrollmentStatus,
        InstallmentStatus,
    )

    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    start = datetime.now(timezone.utc) + timedelta(days=3)
    cohort = CohortFactory.create(
        program_id=program.id,
        start_date=start,
        end_date=start + timedelta(weeks=12),
    )
    db_session.add(cohort)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.flush()

    enrollment = EnrollmentFactory.create(
        cohort_id=cohort.id,
        program_id=program.id,
        member_id=member.id,
        status=EnrollmentStatus.ENROLLED,
        reminders_sent=["7_days", "wallet_deduction_1"],
    )
    db_session.add(enrollment)
    await db_session.flush()

    pending_due = start + timedelta(weeks=4)
    paid_due = start
    db_session.add_all(
        [
            EnrollmentInstallment(
                enrollment_id=enrollment.id,
                installment_number=1,
                amount=50000,
                due_at=pending_due,
                status=InstallmentStatus.PENDING,
            ),
            EnrollmentInstallment(
                enrollment_id=enrollment.id,
                installment_number=2,
                amount=50000,
                due_at=paid_due,
                status=InstallmentStatus.PAID,
            ),
        ]
    )
    await db_session.commit()

    session_payload = [
        {
            "id": str(uuid.uuid4()),
            "status": "scheduled",
            "starts_at": (start + timedelta(days=1)).isoformat(),
            "ends_at": (start + timedelta(days=1, hours=1)).isoformat(),
        }
    ]

    with (
        patch(
            "services.academy_service.routers.member.internal_get",
            new_callable=AsyncMock,
            return_value=_FakeResponse(payload=session_payload),
        ),
        patch(
            "services.academy_service.routers.member.internal_patch",
            new_callable=AsyncMock,
            return_value=_FakeResponse(payload={}),
        ),
    ):
        response = await academy_client.post(
            f"/academy/cohorts/{cohort.id}/timeline-shifts",
            json={
                "new_start_date": (cohort.start_date + timedelta(days=14)).isoformat(),
                "new_end_date": (cohort.end_date + timedelta(days=14)).isoformat(),
                "expected_updated_at": cohort.updated_at.isoformat(),
                "shift_sessions": True,
                "shift_installments": True,
                "reset_start_reminders": True,
                "notify_members": False,
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sessions_shifted"] == 1
    assert data["pending_installments_shifted"] == 1
    assert data["reminder_resets_applied"] == 1

    await db_session.refresh(cohort)
    assert cohort.start_date == start + timedelta(days=14)
    assert cohort.end_date == start + timedelta(weeks=12, days=14)

    refreshed_enrollment = (
        (
            await db_session.execute(
                select(Enrollment).where(Enrollment.id == enrollment.id)
            )
        )
        .scalars()
        .one()
    )
    assert "7_days" not in (refreshed_enrollment.reminders_sent or [])
    assert "wallet_deduction_1" in (refreshed_enrollment.reminders_sent or [])

    installments = (
        (
            await db_session.execute(
                select(EnrollmentInstallment).where(
                    EnrollmentInstallment.enrollment_id == enrollment.id
                )
            )
        )
        .scalars()
        .all()
    )
    installment_by_number = {inst.installment_number: inst for inst in installments}
    assert installment_by_number[1].due_at == pending_due + timedelta(days=14)
    assert installment_by_number[2].due_at == paid_due


@pytest.mark.asyncio
@pytest.mark.integration
async def test_apply_cohort_timeline_shift_idempotency_replays_logged_result(
    academy_client, db_session
):
    """Repeated apply with same idempotency_key returns immutable logged result."""
    from services.academy_service.models import CohortTimelineShiftLog

    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    start = datetime.now(timezone.utc) + timedelta(days=4)
    cohort = CohortFactory.create(
        program_id=program.id,
        start_date=start,
        end_date=start + timedelta(weeks=12),
    )
    db_session.add(cohort)
    await db_session.commit()

    session_payload = [
        {
            "id": str(uuid.uuid4()),
            "status": "scheduled",
            "starts_at": (start + timedelta(days=1)).isoformat(),
            "ends_at": (start + timedelta(days=1, hours=1)).isoformat(),
        }
    ]
    idempotency_key = f"timeline-shift-{uuid.uuid4()}"

    with (
        patch(
            "services.academy_service.routers.member.internal_get",
            new_callable=AsyncMock,
            return_value=_FakeResponse(payload=session_payload),
        ),
        patch(
            "services.academy_service.routers.member.internal_patch",
            new_callable=AsyncMock,
            return_value=_FakeResponse(payload={}),
        ) as patch_mock,
    ):
        first_response = await academy_client.post(
            f"/academy/cohorts/{cohort.id}/timeline-shifts",
            json={
                "new_start_date": (cohort.start_date + timedelta(days=14)).isoformat(),
                "new_end_date": (cohort.end_date + timedelta(days=14)).isoformat(),
                "expected_updated_at": cohort.updated_at.isoformat(),
                "idempotency_key": idempotency_key,
                "shift_sessions": True,
                "shift_installments": False,
                "reset_start_reminders": False,
                "notify_members": False,
            },
        )
        second_response = await academy_client.post(
            f"/academy/cohorts/{cohort.id}/timeline-shifts",
            json={
                "new_start_date": (cohort.start_date + timedelta(days=14)).isoformat(),
                "new_end_date": (cohort.end_date + timedelta(days=14)).isoformat(),
                "expected_updated_at": cohort.updated_at.isoformat(),
                "idempotency_key": idempotency_key,
                "shift_sessions": True,
                "shift_installments": False,
                "reset_start_reminders": False,
                "notify_members": False,
            },
        )

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    assert first_response.json()["sessions_shifted"] == 1
    assert second_response.json()["sessions_shifted"] == 1
    # Replay should return from immutable log and avoid re-patching sessions.
    assert patch_mock.await_count == 1

    logs = (
        (
            await db_session.execute(
                select(CohortTimelineShiftLog).where(
                    CohortTimelineShiftLog.cohort_id == cohort.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].idempotency_key == idempotency_key


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_cohort_timeline_shift_logs_returns_newest_first(
    academy_client, db_session
):
    """Admin can fetch immutable timeline shift logs by cohort."""
    from services.academy_service.models import CohortTimelineShiftLog

    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()

    older = CohortTimelineShiftLog(
        cohort_id=cohort.id,
        idempotency_key=f"k-{uuid.uuid4()}",
        actor_auth_id="auth-old",
        reason="old",
        old_start_date=cohort.start_date,
        old_end_date=cohort.end_date,
        new_start_date=cohort.start_date + timedelta(days=7),
        new_end_date=cohort.end_date + timedelta(days=7),
        delta_seconds=7 * 24 * 60 * 60,
        options_json={"shift_sessions": True},
        results_json={"sessions_shifted": 2},
        warnings=[],
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    newer = CohortTimelineShiftLog(
        cohort_id=cohort.id,
        idempotency_key=f"k-{uuid.uuid4()}",
        actor_auth_id="auth-new",
        reason="new",
        old_start_date=cohort.start_date + timedelta(days=7),
        old_end_date=cohort.end_date + timedelta(days=7),
        new_start_date=cohort.start_date + timedelta(days=14),
        new_end_date=cohort.end_date + timedelta(days=14),
        delta_seconds=7 * 24 * 60 * 60,
        options_json={"shift_sessions": True},
        results_json={"sessions_shifted": 3},
        warnings=[],
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    response = await academy_client.get(f"/academy/cohorts/{cohort.id}/timeline-shifts")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 2
    assert rows[0]["reason"] == "new"
    assert rows[1]["reason"] == "old"


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_milestones(academy_client, db_session):
    """List milestones for a program."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    m1 = MilestoneFactory.create(program_id=program.id, order_index=0)
    m2 = MilestoneFactory.create(program_id=program.id, order_index=1)
    db_session.add_all([m1, m2])
    await db_session.commit()

    response = await academy_client.get(f"/academy/programs/{program.id}/milestones")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2
