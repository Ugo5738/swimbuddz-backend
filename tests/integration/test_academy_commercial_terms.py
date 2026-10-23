"""Negotiated terms keep settled enrollment history and receipts intact."""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
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


@pytest.fixture
async def unpaid_enrollment(db_session, monkeypatch):
    member = MemberFactory.create()
    program = ProgramFactory.create(price_amount=24_000_000, duration_weeks=12)
    cohort = CohortFactory.create(
        program_id=program.id,
        price_override=16_500_000,
        installment_plan_enabled=True,
        installment_count=3,
        installment_deposit_amount=5_500_000,
        start_date=utc_now() - timedelta(weeks=6),
    )
    db_session.add_all([member, program, cohort])
    await db_session.flush()
    enrollment = EnrollmentFactory.create(
        member_id=member.id,
        program_id=program.id,
        cohort_id=cohort.id,
        price_snapshot_amount=16_500_000,
        payment_status=PaymentStatus.PENDING,
        membership_policy_snapshot="open",
        uses_installments=False,
    )
    db_session.add(enrollment)
    await db_session.commit()
    monkeypatch.setattr(
        commercial_terms,
        "financial_state",
        AsyncMock(
            return_value={
                "paid_transfer_eligible": True,
                "verified_paid_tuition_kobo": 0,
                "references": [],
                "has_payment_activity": False,
            }
        ),
    )
    return enrollment


def new_terms(preview, **changes):
    return commercial_terms.CommercialTermsRequest(
        **{
            "adjustment_id": uuid.uuid4(),
            "reason": "Approved Festac tuition 125000 with installments 45000, 45000, 35000",
            "expected_price_snapshot_kobo": preview["price_snapshot_kobo"],
            "expected_installments": preview["installments"],
            "unpaid_installment_amounts_kobo": [4_500_000, 4_500_000, 3_500_000],
            "historic_discount_kobo": 0,
            "agreed_cash_total_kobo": 12_500_000,
            "billing_mode": "installments",
            **changes,
        }
    )


async def test_preview_does_not_create_a_plan_or_change_price(
    db_session, unpaid_enrollment
):
    preview = await commercial_terms.preview_commercial_terms(
        unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
    )
    assert preview["installments"] == []
    assert [i["amount_kobo"] for i in preview["suggested_installments"]] == [
        5_500_000
    ] * 3
    assert unpaid_enrollment.price_snapshot_amount == 16_500_000
    assert not unpaid_enrollment.uses_installments
    assert not (
        await db_session.scalars(
            select(EnrollmentInstallment).where(
                EnrollmentInstallment.enrollment_id == unpaid_enrollment.id
            )
        )
    ).all()


@pytest.mark.parametrize("existing_plan", [False, True])
async def test_agree_125k_before_any_payment_and_quote_first_45k(
    db_session, unpaid_enrollment, existing_plan, monkeypatch
):
    from services.academy_service.routers.internal import preview_enrollment_payment

    if existing_plan:
        for number in range(1, 4):
            db_session.add(
                EnrollmentInstallment(
                    enrollment_id=unpaid_enrollment.id,
                    installment_number=number,
                    amount=5_500_000,
                    due_at=unpaid_enrollment.enrolled_at,
                    status=InstallmentStatus.PENDING,
                )
            )
        await db_session.commit()
    preview = await commercial_terms.preview_commercial_terms(
        unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
    )
    payload = new_terms(preview, **({"billing_mode": None} if existing_plan else {}))
    result = await commercial_terms.approve_commercial_terms(
        unpaid_enrollment.id, payload, AuthUser(user_id="admin"), db_session
    )
    assert result["remaining_cash_kobo"] == 12_500_000
    assert unpaid_enrollment.price_snapshot_amount == 12_500_000
    assert unpaid_enrollment.payment_status == PaymentStatus.PENDING
    assert unpaid_enrollment.paid_installments_count == 0
    db_session.expire(unpaid_enrollment, ["installments"])
    checkout = await preview_enrollment_payment(
        unpaid_enrollment.id,
        use_installments=True,
        _=AuthUser(user_id="service"),
        db=db_session,
    )
    assert checkout["price_snapshot_amount"] == 12_500_000
    assert [i["amount"] for i in checkout["installments"]] == [
        4_500_000,
        4_500_000,
        3_500_000,
    ]
    assert all(
        i["status"] == "pending" and i["payment_reference"] is None
        for i in checkout["installments"]
    )
    if not existing_plan:
        from services.academy_service.services.installments import monday_00_wat

        assert datetime.fromisoformat(
            checkout["installments"][0]["due_at"]
        ) == monday_00_wat(unpaid_enrollment.enrolled_at)
    from services.payments_service.services import academy_pricing

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = httpx.Response(200, json=checkout)
    monkeypatch.setattr(academy_pricing.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(
        academy_pricing,
        "get_member_by_auth_id",
        AsyncMock(
            return_value={
                "id": str(unpaid_enrollment.member_id),
                "membership": {},
            }
        ),
    )
    for installments, expected in [(True, 4_500_000), (False, 12_500_000)]:
        quote = await academy_pricing.academy_payment_context(
            enrollment_id=unpaid_enrollment.id,
            member_auth_id=unpaid_enrollment.member_auth_id,
            use_installments=installments,
            preview_only=True,
        )
        assert quote["academy_amount_kobo"] == expected
    report = await commercial_terms.academy_contract_summary(
        cohort_id=unpaid_enrollment.cohort_id,
        _admin=AuthUser(user_id="admin"),
        db=db_session,
    )
    assert report["commercial_concessions_kobo"] == 4_000_000
    assert report["contracted_cash_value_kobo"] == 12_500_000
    record = (
        await db_session.scalars(
            select(AcademyCommercialAdjustment).where(
                AcademyCommercialAdjustment.enrollment_id == unpaid_enrollment.id
            )
        )
    ).one()
    assert record.original_terms["price_snapshot_kobo"] == 16_500_000
    assert record.approved_terms["historic_discount_kobo"] == 0
    replay = await commercial_terms.approve_commercial_terms(
        unpaid_enrollment.id, payload, AuthUser(user_id="admin"), db_session
    )
    assert replay["idempotent"]


async def test_agree_full_tuition_before_payment(db_session, unpaid_enrollment):
    preview = await commercial_terms.preview_commercial_terms(
        unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
    )
    payload = new_terms(
        preview, billing_mode="full", unpaid_installment_amounts_kobo=[12_500_000]
    )
    await commercial_terms.approve_commercial_terms(
        unpaid_enrollment.id, payload, AuthUser(user_id="admin"), db_session
    )
    assert unpaid_enrollment.price_snapshot_amount == 12_500_000
    assert not unpaid_enrollment.uses_installments
    assert unpaid_enrollment.payment_status == PaymentStatus.PENDING
    assert not (
        await db_session.scalars(
            select(EnrollmentInstallment).where(
                EnrollmentInstallment.enrollment_id == unpaid_enrollment.id
            )
        )
    ).all()


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"historic_discount_kobo": 1}, 409),
        ({"agreed_cash_total_kobo": 12_400_000}, 409),
        (
            {
                "agreed_cash_total_kobo": 17_000_000,
                "unpaid_installment_amounts_kobo": [6_000_000, 6_000_000, 5_000_000],
            },
            409,
        ),
        ({"unpaid_installment_amounts_kobo": [1_000_000, 6_000_000, 5_500_000]}, 422),
        ({"billing_mode": "full"}, 422),
        ({"billing_mode": None}, 422),
    ],
)
async def test_reject_invalid_unpaid_agreement(
    db_session, unpaid_enrollment, changes, code
):
    preview = await commercial_terms.preview_commercial_terms(
        unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
    )
    with pytest.raises(HTTPException) as err:
        await commercial_terms.approve_commercial_terms(
            unpaid_enrollment.id,
            new_terms(preview, **changes),
            AuthUser(user_id="admin"),
            db_session,
        )
    assert err.value.status_code == code
    assert unpaid_enrollment.price_snapshot_amount == 16_500_000


async def test_pending_attempt_blocks_pre_payment_terms(
    db_session, unpaid_enrollment, monkeypatch
):
    preview = await commercial_terms.preview_commercial_terms(
        unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
    )
    monkeypatch.setattr(
        commercial_terms,
        "financial_state",
        AsyncMock(
            return_value={
                "paid_transfer_eligible": False,
                "verified_paid_tuition_kobo": 0,
                "references": ["pending-checkout"],
            }
        ),
    )
    with pytest.raises(HTTPException) as err:
        await commercial_terms.approve_commercial_terms(
            unpaid_enrollment.id,
            new_terms(preview),
            AuthUser(user_id="admin"),
            db_session,
        )
    assert err.value.status_code == 409
    assert unpaid_enrollment.price_snapshot_amount == 16_500_000


async def test_full_paid_enrollment_cannot_be_repriced(db_session, unpaid_enrollment):
    unpaid_enrollment.payment_status = PaymentStatus.PAID
    await db_session.commit()
    with pytest.raises(HTTPException) as err:
        await commercial_terms.preview_commercial_terms(
            unpaid_enrollment.id, AuthUser(user_id="admin"), db_session
        )
    assert err.value.status_code == 409


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
