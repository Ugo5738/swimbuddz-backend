"""Tuition credits are idempotent, never additional bank cash-in."""
import uuid

import pytest
from sqlalchemy import select

from services.academy_service.models import AcademyFinancialCredit, EnrollmentInstallment
from tests.factories import MemberFactory, ProgramFactory, CohortFactory, EnrollmentFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_receipt_credit_reduces_exact_outstanding_tuition(academy_client, db_session):
    member = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, program])
    await db_session.flush()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        member_id=member.id,
        member_auth_id="academy-credit-member",
        program_id=program.id,
        cohort_id=cohort.id,
        price_snapshot_amount=16_500_000,
        currency_snapshot="NGN",
    )
    db_session.add(enrollment)
    await db_session.commit()

    payload = {
        "source_reference": f"academy-receipt-allocation:{uuid.uuid4()}",
        "source_kind": "receipt_allocation",
        "member_auth_id": "academy-credit-member",
        "amount_kobo": 5_000_000,
        "actor_auth_id": "finance-admin",
    }
    url = f"/internal/academy/enrollments/{enrollment.id}/verified-credit"
    first = await academy_client.post(url, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False
    second = await academy_client.post(url, json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    credits = (await db_session.execute(
        select(AcademyFinancialCredit)
        .where(AcademyFinancialCredit.enrollment_id == enrollment.id)
    )).scalars().all()
    assert len(credits) == 1
    assert credits[0].amount_kobo == 5_000_000
    installments = (await db_session.execute(
        select(EnrollmentInstallment)
        .where(EnrollmentInstallment.enrollment_id == enrollment.id)
    )).scalars().all()
    assert sum(row.amount for row in installments if row.status.value != "paid") == 11_500_000


async def test_credit_belonging_to_other_member_is_forbidden(academy_client, db_session):
    member = MemberFactory.create()
    program = ProgramFactory.create()
    db_session.add_all([member, program])
    await db_session.flush()
    cohort = CohortFactory.create(program_id=program.id)
    db_session.add(cohort)
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        member_id=member.id,
        member_auth_id="correct-member",
        program_id=program.id,
        cohort_id=cohort.id,
        price_snapshot_amount=16_500_000,
    )
    db_session.add(enrollment)
    await db_session.commit()
    response = await academy_client.post(
        f"/internal/academy/enrollments/{enrollment.id}/verified-credit",
        json={
            "source_reference": f"academy-receipt-allocation:{uuid.uuid4()}",
            "source_kind": "receipt_allocation",
            "member_auth_id": "wrong-member",
            "amount_kobo": 5_000_000,
            "actor_auth_id": "finance-admin",
        },
    )
    assert response.status_code == 403, response.text
