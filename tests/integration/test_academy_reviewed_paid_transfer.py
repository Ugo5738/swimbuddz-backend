"""Reviewed Academy transfer must move tuition value, not duplicate bank income."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from libs.auth.models import AuthUser
from services.academy_service.models import (
    AcademyJourney,
    AcademyEnrollmentChange,
    AcademyFinancialCredit,
    AcademyTransferRefundObligation,
    Enrollment,
    EnrollmentStatus,
    PaymentStatus,
)
from services.academy_service.routers.enrollments import paid_transfer
from tests.factories import (
    MemberFactory,
    ProgramFactory,
    CohortFactory,
    EnrollmentFactory,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    "transfer_kobo,refund_kobo",
    [(5_000_000, 0), (3_000_000, 2_000_000)],
)
async def test_50000_verified_credit_moves_once_from_vi_to_yaba(
    db_session,
    academy_client,
    monkeypatch,
    transfer_kobo,
    refund_kobo,
):
    member = MemberFactory.create()
    programme = ProgramFactory.create(price_amount=24_000_000)
    db_session.add_all([member, programme])
    await db_session.flush()
    vi = CohortFactory.create(program_id=programme.id, name="Sep 2026 VI")
    yaba = CohortFactory.create(
        program_id=programme.id,
        name="Oct 2026 Yaba",
        price_override=16_500_000,
    )
    db_session.add_all([vi, yaba])
    await db_session.flush()
    source = EnrollmentFactory.create(
        member_id=member.id,
        member_auth_id="student-a",
        program_id=programme.id,
        cohort_id=vi.id,
        status=EnrollmentStatus.PENDING_APPROVAL,
        payment_status=PaymentStatus.PENDING,
        price_snapshot_amount=23_500_000,
        currency_snapshot="NGN",
    )
    db_session.add(source)
    await db_session.commit()
    credit_response = await academy_client.post(
        f"/internal/academy/enrollments/{source.id}/verified-credit",
        json={
            "source_reference": f"academy-receipt-allocation:{uuid.uuid4()}",
            "source_kind": "receipt_allocation",
            "member_auth_id": "student-a",
            "amount_kobo": 5_000_000,
            "actor_auth_id": "admin",
        },
    )
    assert credit_response.status_code == 200, credit_response.text
    journey = AcademyJourney(member_id=member.id, program_id=programme.id)
    db_session.add(journey)
    await db_session.flush()
    change = AcademyEnrollmentChange(
        journey_id=journey.id,
        from_enrollment_id=source.id,
        target_cohort_id=yaba.id,
        actor_auth_id="student-a",
        state="needs_review",
        snapshot={"target_cohort_id": str(yaba.id)},
    )
    db_session.add(change)
    await db_session.commit()
    monkeypatch.setattr(
        paid_transfer,
        "financial_state",
        AsyncMock(
            return_value={
                "paid_transfer_eligible": True,
                "verified_paid_tuition_kobo": 0,
                "references": [],
                "attempts": [],
            }
        ),
    )
    payload = paid_transfer.ApproveReviewedTransfer(
        reason="One receipt allocated and unused tuition transferred after review",
        transferable_credit_kobo=transfer_kobo,
        consumed_services_kobo=0,
        refund_due_kobo=refund_kobo,
        refund_reason="Reviewed unspent tuition refund remains payable" if refund_kobo else None,
        discount_kobo=0,
        confirmed_attendance_review=True,
    )
    result = await paid_transfer.approve_reviewed_transfer(
        change.id,
        payload,
        AuthUser(user_id="admin"),
        db_session,
    )
    assert result["state"] == "completed"
    assert result["remaining_tuition_kobo"] == 16_500_000 - transfer_kobo
    assert result["refund_due_kobo"] == refund_kobo
    assert result["idempotent"] is False
    old = (
        await db_session.execute(select(Enrollment).where(Enrollment.id == source.id))
    ).scalar_one()
    assert old.status == EnrollmentStatus.DROPPED
    new_id = uuid.UUID(result["enrollment_id"])
    new = (
        await db_session.execute(select(Enrollment).where(Enrollment.id == new_id))
    ).scalar_one()
    assert new.price_snapshot_amount == 16_500_000
    assert new.cohort_id == yaba.id
    source_credits = (
        (
            await db_session.execute(
                select(AcademyFinancialCredit).where(
                    AcademyFinancialCredit.enrollment_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(source_credits) == 1
    assert source_credits[0].state == "transferred_out"
    new_credits = (
        (
            await db_session.execute(
                select(AcademyFinancialCredit).where(
                    AcademyFinancialCredit.enrollment_id == new_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(new_credits) == 1
    assert new_credits[0].amount_kobo == transfer_kobo
    refund_rows = (
        (
            await db_session.execute(
                select(AcademyTransferRefundObligation).where(
                    AcademyTransferRefundObligation.change_id == change.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refund_rows) == (1 if refund_kobo else 0)
    if refund_rows:
        assert refund_rows[0].state == "pending"
        assert refund_rows[0].amount_kobo == refund_kobo
    replay = await paid_transfer.approve_reviewed_transfer(
        change.id,
        payload,
        AuthUser(user_id="admin"),
        db_session,
    )
    assert replay["idempotent"] is True
    assert replay["enrollment_id"] == result["enrollment_id"]
