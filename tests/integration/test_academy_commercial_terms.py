"""Negotiated terms keep settled enrollment history and receipts intact."""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from libs.auth.models import AuthUser
from services.academy_service.models import (
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
)
from services.academy_service.models.commercial_adjustment import (
    AcademyCommercialAdjustment,
)
from services.academy_service.routers.enrollments import commercial_terms
from tests.factories import (
    MemberFactory,
    ProgramFactory,
    CohortFactory,
    EnrollmentFactory,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_settled_55k_coupon5k_cash50k_future_50k_45k(db_session, monkeypatch):
    member = MemberFactory.create()
    program = ProgramFactory.create(price_amount=16_500_000)
    db_session.add_all([member, program])
    await db_session.flush()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        member_id=member.id,
        member_auth_id="learner",
        program_id=program.id,
        cohort_id=cohort.id,
        status=EnrollmentStatus.ENROLLED,
        price_snapshot_amount=16_500_000,
        uses_installments=True,
    )
    db_session.add(enrollment)
    await db_session.flush()
    for number, paid in [(1, True), (2, False), (3, False)]:
        db_session.add(
            EnrollmentInstallment(
                enrollment_id=enrollment.id,
                installment_number=number,
                amount=5_500_000,
                due_at=cohort.start_date,
                status=InstallmentStatus.PAID if paid else InstallmentStatus.PENDING,
                payment_reference="paid-ref" if paid else None,
            )
        )
    await db_session.commit()
    monkeypatch.setattr(
        commercial_terms,
        "financial_state",
        AsyncMock(
            return_value={
                "paid_transfer_eligible": True,
                "verified_paid_tuition_kobo": 5_000_000,
                "references": ["paid-ref"],
            }
        ),
    )
    preview = await commercial_terms.preview_commercial_terms(
        enrollment.id, AuthUser(user_id="admin"), db_session
    )
    snapshot = preview["installments"]
    payload = commercial_terms.CommercialTermsRequest(
        adjustment_id=uuid.uuid4(),
        reason="Approved couple tuition 145000 after 5000 historical coupon",
        expected_price_snapshot_kobo=16_500_000,
        expected_installments=snapshot,
        unpaid_installment_amounts_kobo=[5_000_000, 4_500_000],
        historic_discount_kobo=500_000,
        agreed_cash_total_kobo=14_500_000,
    )
    result = await commercial_terms.approve_commercial_terms(
        enrollment.id, payload, AuthUser(user_id="admin"), db_session
    )
    assert result["state"] == "completed"
    assert result["remaining_cash_kobo"] == 9_500_000
    assert result["nominal_tuition_kobo"] == 15_000_000
    updated = (
        await db_session.execute(
            select(Enrollment).where(Enrollment.id == enrollment.id)
        )
    ).scalar_one()
    assert updated.price_snapshot_amount == 15_000_000
    rows = (
        (
            await db_session.execute(
                select(EnrollmentInstallment)
                .where(EnrollmentInstallment.enrollment_id == enrollment.id)
                .order_by(EnrollmentInstallment.installment_number)
            )
        )
        .scalars()
        .all()
    )
    assert [row.amount for row in rows] == [5_500_000, 5_000_000, 4_500_000]
    assert rows[0].status == InstallmentStatus.PAID
    assert rows[0].payment_reference == "paid-ref"
    records = (
        (
            await db_session.execute(
                select(AcademyCommercialAdjustment).where(
                    AcademyCommercialAdjustment.enrollment_id == enrollment.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(records) == 1
    replay = await commercial_terms.approve_commercial_terms(
        enrollment.id, payload, AuthUser(user_id="admin"), db_session
    )
    assert replay["idempotent"] is True
    with pytest.raises(HTTPException) as stale:
        await commercial_terms.approve_commercial_terms(
            enrollment.id,
            payload.model_copy(update={"adjustment_id": uuid.uuid4()}),
            AuthUser(user_id="admin"),
            db_session,
        )
    assert stale.value.status_code == 409


async def test_unverified_paid_cash_blocks_adjustment(db_session, monkeypatch):
    member = MemberFactory.create()
    program = ProgramFactory.create(price_amount=16_500_000)
    db_session.add_all([member, program])
    await db_session.flush()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        member_id=member.id,
        member_auth_id="learner",
        program_id=program.id,
        cohort_id=cohort.id,
        status=EnrollmentStatus.ENROLLED,
        price_snapshot_amount=16_500_000,
    )
    db_session.add(enrollment)
    await db_session.flush()
    db_session.add_all(
        [
            EnrollmentInstallment(
                enrollment_id=enrollment.id,
                installment_number=n,
                amount=5_500_000,
                due_at=cohort.start_date,
                status=InstallmentStatus.PAID if n == 1 else InstallmentStatus.PENDING,
            )
            for n in range(1, 4)
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(
        commercial_terms,
        "financial_state",
        AsyncMock(
            return_value={
                "paid_transfer_eligible": False,
                "verified_paid_tuition_kobo": 0,
                "references": [],
            }
        ),
    )
    with pytest.raises(HTTPException) as err:
        await commercial_terms.preview_commercial_terms(
            enrollment.id, AuthUser(user_id="admin"), db_session
        )
    assert err.value.status_code == 409
