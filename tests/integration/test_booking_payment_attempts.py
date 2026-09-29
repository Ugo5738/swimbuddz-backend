"""Booking identity, not browser attempt keys, owns the payable transaction."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers.intents import intent_creation, _paystack
from services.payments_service.routers.intents._entitlement import _dispatcher
from services.payments_service.schemas import CreatePaymentIntentRequest
from services.payments_service.services.booking_payment_attempts import booking_payments

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def checkout(monkeypatch):
    booking_id, session_id, member_id = uuid4(), uuid4(), uuid4()
    user = AuthUser(user_id=str(member_id), email="payer@example.com")
    quote = AsyncMock(
        return_value={
            "id": str(booking_id),
            "session_id": str(session_id),
            "member_id": str(member_id),
            "fee_amount_kobo": 520000,
            "expires_at": (utc_now() + timedelta(minutes=15)).isoformat(),
        }
    )
    monkeypatch.setattr(intent_creation, "_get_session_booking_quote", quote)
    initialize = AsyncMock(
        return_value=("https://checkout.example.test/frozen", "frozen")
    )
    monkeypatch.setattr(intent_creation, "_initialize_paystack", initialize)
    monkeypatch.setattr(_paystack, "_initialize_paystack", initialize)
    monkeypatch.setattr(intent_creation, "_paystack_enabled", lambda: True)
    monkeypatch.setattr(_paystack, "_paystack_enabled", lambda: True)
    monkeypatch.setattr(
        intent_creation, "_set_pending_tier_payment_for_payment", AsyncMock()
    )
    monkeypatch.setattr(
        intent_creation,
        "create_wallet_hold",
        AsyncMock(return_value={"id": str(uuid4())}),
    )
    payload = CreatePaymentIntentRequest(
        purpose="session_booking",
        session_id=session_id,
        direct_amount=5200,
        payment_metadata={"booking_id": str(booking_id)},
        idempotency_key=str(uuid4()),
    )
    return booking_id, user, payload, initialize, quote


async def test_same_or_different_browser_key_resumes_single_payment(
    db_session, checkout
):
    booking_id, user, payload, initialize, _ = checkout
    first = await intent_creation.create_payment_intent(payload, user, db_session)
    same = await intent_creation.create_payment_intent(payload, user, db_session)
    different = await intent_creation.create_payment_intent(
        payload.model_copy(update={"idempotency_key": str(uuid4())}), user, db_session
    )
    assert first.reference == same.reference == different.reference
    assert len(await booking_payments(db_session, booking_id)) == 1
    initialize.assert_awaited_once()


async def test_internal_initializer_cannot_bypass_existing_booking_payment(
    db_session, checkout
):
    from services.payments_service.routers import internal
    from services.payments_service.schemas import InternalInitializeRequest

    booking_id, user, payload, initialize, _ = checkout
    first = await intent_creation.create_payment_intent(payload, user, db_session)
    resumed = await internal.internal_initialize_payment(
        InternalInitializeRequest(
            purpose="session_booking",
            amount=5200,
            reference=f"another-key-{uuid4()}",
            member_auth_id=user.user_id,
            metadata={
                "booking_id": str(booking_id),
                "session_id": str(payload.session_id),
                "payer_email": user.email,
            },
        ),
        db_session,
    )
    assert resumed.reference == first.reference
    assert len(await booking_payments(db_session, booking_id)) == 1
    initialize.assert_awaited_once()


async def test_legacy_admin_pending_payment_is_reused_not_replaced(
    db_session, checkout
):
    booking_id, user, payload, initialize, _ = checkout
    legacy = Payment(
        reference=f"legacy-{uuid4()}",
        member_auth_id=user.user_id,
        payer_email=user.email,
        purpose=PaymentPurpose.SESSION_BOOKING,
        amount=5200,
        status=PaymentStatus.PENDING,
        payment_method="paystack",
        provider="paystack",
        payment_metadata={
            "booking_id": str(booking_id),
            "session_id": str(payload.session_id),
            "admin_generated": True,
            "paystack": {"authorization_url": "https://checkout.example.test/old"},
        },
    )
    db_session.add(legacy)
    await db_session.commit()
    result = await intent_creation.create_payment_intent(payload, user, db_session)
    assert result.reference == legacy.reference and result.checkout_url.endswith("/old")
    initialize.assert_not_awaited()
    with pytest.raises(HTTPException, match="already open"):
        await intent_creation.create_payment_intent(
            payload.model_copy(update={"bubbles_to_apply": 10}), user, db_session
        )


async def test_paid_but_awaiting_fulfillment_blocks_new_payment(db_session, checkout):
    booking_id, user, payload, initialize, _ = checkout
    result = await intent_creation.create_payment_intent(payload, user, db_session)
    payment = (await booking_payments(db_session, booking_id))[0]
    payment.status = PaymentStatus.PAID
    await db_session.commit()
    receipt = await intent_creation.create_payment_intent(payload, user, db_session)
    assert (
        receipt.reference == result.reference and receipt.status == PaymentStatus.PAID
    )
    assert receipt.checkout_url is None and receipt.entitlement_applied_at is None
    with pytest.raises(HTTPException, match="already paid"):
        await intent_creation.create_payment_intent(
            payload.model_copy(update={"idempotency_key": str(uuid4())}),
            user,
            db_session,
        )
    initialize.assert_awaited_once()


async def test_settled_booking_and_uncertain_provider_never_reinitialized(
    db_session, checkout
):
    booking_id, user, payload, initialize, quote = checkout
    initialize.side_effect = TimeoutError("Provider response lost")
    with pytest.raises(TimeoutError):
        await intent_creation.create_payment_intent(payload, user, db_session)
    with pytest.raises(HTTPException, match="reconciliation"):
        await intent_creation.create_payment_intent(payload, user, db_session)
    assert len(await booking_payments(db_session, booking_id)) == 1
    initialize.assert_awaited_once()
    quote.side_effect = HTTPException(409, "Booking is already settled")
    with pytest.raises(HTTPException, match="already settled"):
        await intent_creation.create_payment_intent(payload, user, db_session)


async def test_partial_bubbles_retry_preserves_one_wallet_hold(
    db_session, checkout, monkeypatch
):
    _, user, payload, initialize, _ = checkout
    wallet_hold = AsyncMock(return_value={"id": str(uuid4())})
    monkeypatch.setattr(intent_creation, "create_wallet_hold", wallet_hold)
    payload = payload.model_copy(update={"bubbles_to_apply": 20})
    first = await intent_creation.create_payment_intent(payload, user, db_session)
    second = await intent_creation.create_payment_intent(
        payload.model_copy(update={"idempotency_key": str(uuid4())}), user, db_session
    )
    assert first.reference == second.reference and second.amount == 3200
    wallet_hold.assert_awaited_once()
    initialize.assert_awaited_once()


async def test_concurrent_requests_cannot_initialize_two_payable_attempts(
    test_engine, checkout
):
    booking_id, user, payload, initialize, _ = checkout
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def request(body):
        async with factory() as db:
            try:
                return await intent_creation.create_payment_intent(body, user, db)
            except HTTPException as error:
                await db.rollback()
                return error.status_code

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                request(payload),
                request(payload.model_copy(update={"idempotency_key": str(uuid4())})),
            ),
            10,
        )
        assert any(getattr(result, "reference", None) for result in results)
        async with factory() as db:
            rows = await booking_payments(db, booking_id)
            assert len(rows) == 1 and rows[0].status == PaymentStatus.PENDING
        initialize.assert_awaited_once()
    finally:
        async with factory() as db:
            await db.execute(
                delete(Payment).where(Payment.member_auth_id == user.user_id)
            )
            await db.commit()


async def test_late_legacy_receipt_recorded_without_duplicate_entitlement(
    db_session, monkeypatch
):
    booking_id = uuid4()
    rows = [
        Payment(
            reference=f"legacy-{uuid4()}",
            member_auth_id="legacy-payer",
            purpose=PaymentPurpose.SESSION_BOOKING,
            amount=5200,
            payment_metadata={"booking_id": str(booking_id)},
            status=PaymentStatus.PENDING,
        )
        for _ in range(2)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    apply = AsyncMock()
    monkeypatch.setattr(_dispatcher, "_apply_entitlement", apply)
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
    for payment in [rows[0], rows[1], rows[1]]:
        await _dispatcher._mark_paid_and_apply(
            db_session, payment, "paystack", payment.reference, utc_now()
        )
    await _dispatcher._apply_entitlement_with_tracking(
        rows[1]
    )  # Admin/worker replay is guarded too.
    assert all(row.status == PaymentStatus.PAID for row in rows)
    assert rows[0].entitlement_applied_at and rows[1].entitlement_applied_at is None
    assert rows[1].payment_metadata["booking_reconciliation"][
        "settled_payment_id"
    ] == str(rows[0].id)
    assert rows[1].payment_metadata["fulfillment"]["status"] == "dead_letter"
    apply.assert_awaited_once()
