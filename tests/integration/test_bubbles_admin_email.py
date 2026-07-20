"""Durable admin receipt delivery for all-Bubbles payments."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from libs.common.datetime_utils import utc_now
from services.payments_service import tasks
from services.payments_service.models import (
    Payment,
    PaymentAdminEmailLog,
    PaymentPurpose,
    PaymentStatus,
)
from sqlalchemy import select


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_bubbles_email_retries_and_is_idempotent(db_session, monkeypatch):
    payment = Payment(
        reference="PAY-BUBBLES-001",
        member_auth_id="auth-bubbles",
        payer_email="member@example.com",
        purpose=PaymentPurpose.SESSION_BOOKING,
        amount=0,
        currency="NGN",
        status=PaymentStatus.PAID,
        provider="internal",
        paid_at=utc_now(),
        entitlement_applied_at=utc_now(),
        payment_metadata={
            "bubbles_to_apply": 35,
            "bubbles_value_ngn": 3500,
        },
        admin_payment_notification_required=True,
    )
    db_session.add(payment)
    await db_session.commit()

    send_template = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(tasks.get_settings(), "ADMIN_EMAILS", ["ops@example.com"])
    monkeypatch.setattr(
        tasks,
        "get_email_client",
        lambda: SimpleNamespace(send_template=send_template),
    )

    first = await tasks._deliver_pending_admin_payment_emails(db_session)
    assert first == {"payments": 1, "sent": 0, "failed": 1}
    log = (
        await db_session.execute(
            select(PaymentAdminEmailLog).where(
                PaymentAdminEmailLog.payment_id == payment.id
            )
        )
    ).scalar_one()
    assert log.delivery_status == "failed"
    assert log.attempt_count == 1

    second = await tasks._deliver_pending_admin_payment_emails(db_session)
    assert second == {"payments": 1, "sent": 1, "failed": 0}
    assert log.delivery_status == "sent"
    assert log.attempt_count == 2
    assert payment.admin_payment_notification_required is False

    third = await tasks._deliver_pending_admin_payment_emails(db_session)
    assert third == {"payments": 0, "sent": 0, "failed": 0}
    assert send_template.await_count == 2
    payload = send_template.await_args.kwargs
    assert payload["template_type"] == "admin_bubbles_payment_received"
    assert payload["template_data"]["bubbles_used"] == 35
