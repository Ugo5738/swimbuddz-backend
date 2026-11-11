"""Unit tests for open booking payment lookup and Paystack method switching."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.payments_service.models import PaymentPurpose, PaymentStatus
from services.payments_service.services import (
    booking_checkout_retry,
    booking_payment_attempts,
)


def _payment(
    *,
    member_auth_id="member-1",
    status=PaymentStatus.PENDING,
    payment_method="paystack",
    provider="paystack",
    purpose=PaymentPurpose.SESSION_BOOKING,
    amount=5200.0,
    metadata=None,
    paid_at=None,
    entitlement_applied_at=None,
):
    return SimpleNamespace(
        id=uuid4(),
        reference=f"PAY-{uuid4().hex}",
        member_auth_id=member_auth_id,
        purpose=purpose,
        status=status,
        payment_method=payment_method,
        provider=provider,
        amount=amount,
        currency="NGN",
        payment_metadata=metadata or {},
        paid_at=paid_at,
        entitlement_applied_at=entitlement_applied_at,
    )


async def test_get_open_payment_returns_safe_summary_and_blocks_cross_user(monkeypatch):
    booking_id = uuid4()
    row = _payment(
        metadata={
            "bubbles_to_apply": 0,
            "paystack": {
                "authorization_url": "https://checkout.paystack.com/abc",
                "access_code": "secret-access-code",
            },
        }
    )
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[row])
    )

    result = await booking_payment_attempts.get_open_booking_payment_for_member(
        SimpleNamespace(), booking_id, "member-1"
    )
    assert result["already_settled"] is False
    open_payment = result["open_payment"]
    assert open_payment["reference"] == row.reference
    assert open_payment["checkout_url"] == "https://checkout.paystack.com/abc"
    assert open_payment["bubbles_to_apply"] == 0
    assert open_payment["can_cancel_and_switch"] is True
    assert "access_code" not in open_payment

    with pytest.raises(HTTPException) as exc:
        await booking_payment_attempts.get_open_booking_payment_for_member(
            SimpleNamespace(), booking_id, "other-member"
        )
    assert exc.value.status_code == 404


async def test_get_open_payment_flags_already_settled_and_protects_manual_transfer(
    monkeypatch,
):
    booking_id = uuid4()
    paid_row = _payment(status=PaymentStatus.PAID)
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[paid_row])
    )
    settled = await booking_payment_attempts.get_open_booking_payment_for_member(
        SimpleNamespace(), booking_id, "member-1"
    )
    assert settled == {"open_payment": None, "already_settled": True}

    transfer_row = _payment(
        status=PaymentStatus.PENDING_REVIEW,
        payment_method="manual_transfer",
        provider=None,
    )
    monkeypatch.setattr(
        booking_payment_attempts,
        "booking_payments",
        AsyncMock(return_value=[transfer_row]),
    )
    transfer_summary = (
        await booking_payment_attempts.get_open_booking_payment_for_member(
            SimpleNamespace(), booking_id, "member-1"
        )
    )
    assert transfer_summary["open_payment"]["can_cancel_and_switch"] is False
    assert transfer_summary["open_payment"]["checkout_url"] is None


async def test_switch_method_closes_abandoned_paystack_and_releases_hold(monkeypatch):
    booking_id = uuid4()
    row = _payment(
        metadata={
            "bubbles_to_apply": 20,
            "wallet_hold_id": "hold-1",
            "paystack": {"authorization_url": "https://checkout.paystack.com/abc"},
        }
    )
    monkeypatch.setattr(booking_payment_attempts, "lock_booking_payment", AsyncMock())
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        booking_checkout_retry,
        "_verify_paystack_transaction",
        AsyncMock(
            return_value={
                "id": 1,
                "reference": row.reference,
                "status": "abandoned",
                "amount": 520000,
                "currency": "NGN",
                "paid_at": None,
            }
        ),
    )
    record_failure = AsyncMock(return_value=True)
    release_hold = AsyncMock()
    clear_pending = AsyncMock()
    monkeypatch.setattr(
        "services.payments_service.services.provider_failure.record_provider_failure",
        record_failure,
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents._helpers._release_bubbles_hold",
        release_hold,
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents._helpers._clear_pending_tier_payment_for_payment",
        clear_pending,
    )

    res = await booking_payment_attempts.switch_open_booking_payment_for_member(
        SimpleNamespace(), booking_id, "member-1"
    )
    assert res["outcome"] == "closed_unpaid"
    assert res["reference"] == row.reference
    record_failure.assert_awaited_once()
    release_hold.assert_awaited_once_with(row)
    clear_pending.assert_awaited_once_with(row)


async def test_switch_method_handles_paystack_404_when_uninitialized(monkeypatch):
    booking_id = uuid4()
    row = _payment(metadata={})
    monkeypatch.setattr(booking_payment_attempts, "lock_booking_payment", AsyncMock())
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        booking_checkout_retry,
        "_verify_paystack_transaction",
        AsyncMock(
            side_effect=HTTPException(
                502, "Paystack verify failed (404): Transaction reference not found"
            )
        ),
    )
    record_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "services.payments_service.services.provider_failure.record_provider_failure",
        record_failure,
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents._helpers._release_bubbles_hold",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents._helpers._clear_pending_tier_payment_for_payment",
        AsyncMock(),
    )

    res = await booking_payment_attempts.switch_open_booking_payment_for_member(
        SimpleNamespace(), booking_id, "member-1"
    )
    assert res["outcome"] == "closed_unpaid"
    record_failure.assert_awaited_once()


async def test_switch_method_fails_closed_when_paystack_unavailable(monkeypatch):
    booking_id = uuid4()
    row = _payment(
        metadata={
            "paystack": {"authorization_url": "https://checkout.paystack.com/abc"}
        }
    )
    monkeypatch.setattr(booking_payment_attempts, "lock_booking_payment", AsyncMock())
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        booking_checkout_retry,
        "_verify_paystack_transaction",
        AsyncMock(side_effect=TimeoutError("network timeout")),
    )

    with pytest.raises(HTTPException) as exc:
        await booking_payment_attempts.switch_open_booking_payment_for_member(
            SimpleNamespace(), booking_id, "member-1"
        )
    assert exc.value.status_code == 503


async def test_switch_method_settles_if_paystack_reports_success(monkeypatch):
    booking_id = uuid4()
    row = _payment(
        metadata={
            "paystack": {"authorization_url": "https://checkout.paystack.com/abc"}
        }
    )
    monkeypatch.setattr(booking_payment_attempts, "lock_booking_payment", AsyncMock())
    monkeypatch.setattr(
        booking_payment_attempts, "booking_payments", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        booking_checkout_retry,
        "_verify_paystack_transaction",
        AsyncMock(
            return_value={
                "id": 1,
                "reference": row.reference,
                "status": "success",
                "amount": 520000,
                "currency": "NGN",
                "paid_at": "2026-11-09T22:40:00Z",
            }
        ),
    )
    mark_paid = AsyncMock()
    monkeypatch.setattr(
        "services.payments_service.routers.intents._entitlement._mark_paid_and_apply",
        mark_paid,
    )

    res = await booking_payment_attempts.switch_open_booking_payment_for_member(
        SimpleNamespace(), booking_id, "member-1"
    )
    assert res["outcome"] == "already_paid"
    mark_paid.assert_awaited_once()
