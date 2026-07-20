"""Admin recording of session fees collected outside platform checkout."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.routers.intents import admin
from sqlalchemy import select


def _booking(booking_id: uuid.UUID) -> dict:
    return {
        "id": str(booking_id),
        "session_id": str(uuid.uuid4()),
        "member_id": str(uuid.uuid4()),
        "member_auth_id": f"auth-{uuid.uuid4()}",
        "status": "confirmed",
        "fee_amount_kobo": 350_000,
    }


async def _settle_without_cross_service_calls(
    db, payment, provider, provider_reference, paid_at, provider_payload
):
    payment.status = PaymentStatus.PAID
    payment.provider = provider
    payment.provider_reference = provider_reference
    payment.paid_at = paid_at
    payment.entitlement_applied_at = utc_now()
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "provider_payload": provider_payload,
    }
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_records_authoritative_offline_booking_payment(
    payments_client, db_session, monkeypatch
):
    booking_id = uuid.uuid4()
    booking = _booking(booking_id)
    monkeypatch.setattr(admin, "get_booking_by_id", AsyncMock(return_value=booking))
    monkeypatch.setattr(
        admin,
        "get_member_by_id",
        AsyncMock(return_value={"email": "member@example.com"}),
    )
    settle = AsyncMock(side_effect=_settle_without_cross_service_calls)
    monkeypatch.setattr(admin, "_mark_paid_and_apply", settle)

    response = await payments_client.post(
        f"/payments/admin/bookings/{booking_id}/offline-payment",
        json={
            "payment_method": "bank_transfer",
            "external_reference": "BANK-TRANSFER-123",
            "note": "Verified against the bank statement.",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == 3500
    assert body["status"] == "paid"
    assert body["payment_method"] == "bank_transfer"
    assert body["session_booking_id"] == str(booking_id)
    assert body["payment_metadata"]["recorded_offline"] is True
    assert body["payment_metadata"]["recorded_by_auth_id"]
    assert settle.await_args.kwargs["provider"] == "offline"
    assert settle.await_args.kwargs["provider_reference"] == "BANK-TRANSFER-123"

    persisted = (
        await db_session.execute(
            select(Payment).where(Payment.session_booking_id == booking_id)
        )
    ).scalar_one()
    assert persisted.amount == 3500
    assert persisted.status == PaymentStatus.PAID


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_cannot_record_booking_payment_twice(payments_client, monkeypatch):
    booking_id = uuid.uuid4()
    monkeypatch.setattr(
        admin, "get_booking_by_id", AsyncMock(return_value=_booking(booking_id))
    )
    monkeypatch.setattr(
        admin,
        "get_member_by_id",
        AsyncMock(return_value={"email": "member@example.com"}),
    )
    monkeypatch.setattr(
        admin,
        "_mark_paid_and_apply",
        AsyncMock(side_effect=_settle_without_cross_service_calls),
    )
    payload = {
        "payment_method": "bank_transfer",
        "external_reference": "BANK-ONE",
    }

    first = await payments_client.post(
        f"/payments/admin/bookings/{booking_id}/offline-payment", json=payload
    )
    second = await payments_client.post(
        f"/payments/admin/bookings/{booking_id}/offline-payment", json=payload
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert "already settled" in second.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_offline_bank_payment_requires_reference(payments_client):
    response = await payments_client.post(
        f"/payments/admin/bookings/{uuid.uuid4()}/offline-payment",
        json={"payment_method": "bank_transfer"},
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_offline_payment_rejects_client_supplied_amount(payments_client):
    response = await payments_client.post(
        f"/payments/admin/bookings/{uuid.uuid4()}/offline-payment",
        json={
            "payment_method": "bank_transfer",
            "external_reference": "BANK-TAMPERED",
            "amount_naira": 1,
        },
    )

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_offline_payment_rejects_future_receipt(payments_client, monkeypatch):
    booking_id = uuid.uuid4()
    monkeypatch.setattr(
        admin, "get_booking_by_id", AsyncMock(return_value=_booking(booking_id))
    )
    monkeypatch.setattr(
        admin,
        "get_member_by_id",
        AsyncMock(return_value={"email": "member@example.com"}),
    )

    response = await payments_client.post(
        f"/payments/admin/bookings/{booking_id}/offline-payment",
        json={
            "payment_method": "cash",
            "received_at": (utc_now() + timedelta(hours=1)).isoformat(),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "received_at cannot be in the future"
