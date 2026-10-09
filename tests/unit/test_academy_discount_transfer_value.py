"""An Academy checkout voucher is not additional received tuition cash."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.payments_service.models import PaymentStatus
from services.payments_service.routers import internal


@pytest.mark.asyncio
async def test_transferable_tuition_excludes_discount_from_paid_installment():
    payment = SimpleNamespace(
        reference="PAY-DISCOUNTED-ACADEMY",
        status=PaymentStatus.PAID,
        amount=50000.0,
        currency="NGN",
        payment_metadata={
            "enrollment_id": str(uuid4()),
            "academy_payment_amount_kobo": 5_500_000,
            "discount_applied": 5000,
            "discount_code": "COUPLE-FIRST-5K",
        },
        proof_of_payment_media_id=None,
        entitlement_applied_at=internal.utc_now(),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [payment])
            )
        )
    )

    result = await internal.academy_enrollment_financial_state(uuid4(), db)
    assert result.verified_paid_tuition_kobo == 5_000_000
    assert result.paid_transfer_eligible is True
    assert result.references == ["PAY-DISCOUNTED-ACADEMY"]
