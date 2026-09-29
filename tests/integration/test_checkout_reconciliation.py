"""Preview and audited closure cannot fabricate or erase a historical receipt."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers import checkout_reconciliation as admin
from services.payments_service.routers.intents._entitlement import _dispatcher
from services.payments_service.services.booking_payment_attempts import (
    blocks_new_attempt,
)
from services.payments_service.services.provider_failure import record_provider_failure

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_preview_closure_replay_and_late_receipt(db_session, monkeypatch):
    row = Payment(
        reference=f"legacy-{uuid4()}",
        purpose=PaymentPurpose.SESSION_BOOKING,
        status=PaymentStatus.PENDING,
        amount=5200,
        currency="NGN",
        member_auth_id="member",
        provider="paystack",
        payment_metadata={
            "booking_id": str(uuid4()),
            "original_note": "Keep audit history",
        },
    )
    db_session.add(row)
    await db_session.commit()
    actor = AuthUser(user_id="admin")
    preview = await admin.preview_checkout(row.reference, db_session)
    body = admin.CloseUnpaidCheckout(
        preview_token=preview["preview_token"],
        provider_closure_evidence="Provider support closure ticket CASE-1234",
        note="Confirmed provider disabled this unpaid checkout",
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents._helpers._release_bubbles_hold",
        AsyncMock(),
    )
    assert not (
        await admin.close_unpaid_checkout(row.reference, body, actor, db_session)
    )["applied"]
    assert row.status == PaymentStatus.PENDING
    body.apply = True
    assert (await admin.close_unpaid_checkout(row.reference, body, actor, db_session))[
        "applied"
    ]
    assert (await admin.close_unpaid_checkout(row.reference, body, actor, db_session))[
        "applied"
    ]
    assert row.status == PaymentStatus.FAILED and not blocks_new_attempt(row)
    assert (
        row.amount == 5200
        and row.payment_metadata["original_note"] == "Keep audit history"
    )
    handler = AsyncMock()
    # A replacement was legitimately prepared after verified closure, before
    # the old provider unexpectedly reported money received.
    replacement = Payment(
        reference=f"replacement-{uuid4()}",
        purpose=PaymentPurpose.SESSION_BOOKING,
        status=PaymentStatus.PENDING,
        amount=5200,
        member_auth_id="member",
        payment_metadata={"booking_id": row.payment_metadata["booking_id"]},
    )
    db_session.add(replacement)
    await db_session.commit()
    monkeypatch.setattr(_dispatcher, "_apply_entitlement", handler)
    for name in (
        "_clear_pending_tier_payment_for_payment",
        "_send_membership_activation_email",
        "_try_qualify_referral",
        "_emit_membership_reward_events",
        "_dispatch_payment_notification",
    ):
        monkeypatch.setattr(_dispatcher, name, AsyncMock())
    monkeypatch.setattr(
        "services.payments_service.services.ledger_emit.emit_payment_to_ledger",
        AsyncMock(),
    )
    await _dispatcher._mark_paid_and_apply(
        db_session, row, "paystack", row.reference, utc_now()
    )
    assert row.status == PaymentStatus.PAID and row.entitlement_applied_at is None
    assert row.payment_metadata["checkout_reconciliation"]
    handler.assert_not_awaited()
    await _dispatcher._mark_paid_and_apply(
        db_session, replacement, "paystack", replacement.reference, utc_now()
    )
    assert replacement.entitlement_applied_at
    handler.assert_awaited_once()
    assert not replacement.payment_metadata.get("booking_reconciliation")


async def test_stale_preview_and_paid_receipt_cannot_be_closed(db_session):
    row = Payment(
        reference=f"legacy-{uuid4()}",
        purpose=PaymentPurpose.SESSION_BOOKING,
        status=PaymentStatus.PENDING,
        amount=5200,
        member_auth_id="member",
    )
    db_session.add(row)
    await db_session.commit()
    body = admin.CloseUnpaidCheckout(
        preview_token="stale",
        provider_closure_evidence="Evidence from provider support",
        note="No money received for this transaction",
        apply=True,
    )
    with pytest.raises(HTTPException, match="preview again"):
        await admin.close_unpaid_checkout(
            row.reference, body, AuthUser(user_id="admin"), db_session
        )
    row.status = PaymentStatus.PAID
    await db_session.commit()
    with pytest.raises(HTTPException, match="paid or fulfilled"):
        await admin.close_unpaid_checkout(
            row.reference, body, AuthUser(user_id="admin"), db_session
        )
    assert row.status == PaymentStatus.PAID
    assert not await record_provider_failure(db_session, row, {"event": "late-failure"})
    assert row.status == PaymentStatus.PAID
