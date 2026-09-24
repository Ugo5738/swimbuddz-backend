"""Manual checkout shares frozen pricing and settlement, never trust a receipt as cash."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers import internal, manual_recording
from services.payments_service.routers.intents import _entitlement
from services.payments_service.schemas import InternalInitializeRequest
from services.payments_service.schemas.manual_recording import (
    AttachPaymentReceipt,
    OfflinePaymentRecord,
    TransferAccess,
    TransferReceipt,
)
from services.payments_service.services import manual_transfer
from tests.conftest import make_admin_user


def row(**changes):
    values = dict(
        id=uuid4(),
        reference="PAY-TRANSFER",
        member_auth_id="member",
        purpose=PaymentPurpose.COMMUNITY,
        amount=20000,
        currency="NGN",
        status=PaymentStatus.PENDING,
        payment_method="manual_transfer",
        payment_metadata={},
        proof_of_payment_media_id=None,
        entitlement_applied_at=None,
        session_booking_id=None,
        provider=None,
        provider_reference=None,
    )
    return Payment(**{**values, **changes})


def result(value):
    return SimpleNamespace(scalar_one=lambda: value, scalar_one_or_none=lambda: value)


def db_for(payment):
    return SimpleNamespace(
        execute=AsyncMock(return_value=result(payment)),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        add=Mock(),
    )


def receipt(**changes):
    return OfflinePaymentRecord(
        **{
            "amount_kobo": 2000000,
            "payment_method": "bank_transfer",
            "received_at": utc_now() - timedelta(days=1),
            "external_reference": "BANK-TEST-001",
            "note": "Verified against bank statement",
            **changes,
        }
    )


def test_transfer_capability_is_payment_scoped_and_not_an_auth_token():
    token = manual_transfer.transfer_token("PAY-ONE")
    assert len(token) == 64 and "." not in token
    manual_transfer.check_transfer_token("PAY-ONE", token)
    assert "#token=" in manual_transfer.transfer_checkout_url("PAY-ONE")
    for invalid in (token, "é" * 64):
        with pytest.raises(HTTPException) as error:
            manual_transfer.check_transfer_token("PAY-TWO", invalid)
        assert error.value.status_code == 404


@pytest.mark.parametrize(
    "purpose",
    [
        "community",
        "club",
        "club_bundle",
        "session_fee",
        "session_booking",
        "session_bundle",
        "guest_pass",
        "community_experience",
        "store_order",
        "wallet_topup",
        "strokelab_founding",
    ],
)
async def test_internal_manual_checkout_persists_quote_without_paystack(
    monkeypatch, purpose
):
    db = db_for(None)
    charges = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(internal, "calculate_additional_charges", charges)
    monkeypatch.setattr(internal, "_paystack_enabled", lambda: False)
    monkeypatch.setattr(
        internal.httpx,
        "AsyncClient",
        Mock(side_effect=AssertionError("No provider call")),
    )
    response = await internal.internal_initialize_payment(
        InternalInitializeRequest(
            purpose=purpose,
            payment_method="manual_transfer",
            amount=20000,
            currency="NGN",
            reference="PAY-TRANSFER",
            member_auth_id="member",
        ),
        db,
    )
    assert response.authorization_url.startswith(
        "/payments/transfer/PAY-TRANSFER#token="
    )
    assert response.amount_kobo == 2000000
    saved = db.add.call_args.args[0]
    assert saved.status == PaymentStatus.PENDING
    assert saved.payment_method == "manual_transfer" and saved.provider is None
    assert saved.payment_metadata["internal_checkout"]["amount_kobo"] == 2000000
    assert charges.await_args.kwargs["payment_method"] == "manual_transfer"


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"metadata": {"wallet_hold_id": "hold"}},
        {"metadata": {"bubbles_to_apply": 5}},
        {"bubbles_to_apply": 5},
    ],
)
async def test_internal_manual_checkout_rejects_unsupported_mix(changes):
    db = db_for(None)
    request = InternalInitializeRequest(
        **{
            "purpose": "wallet_topup",
            "payment_method": "manual_transfer",
            "amount": 20000,
            "reference": "PAY-TRANSFER",
            "member_auth_id": "member",
            **changes,
        }
    )
    with pytest.raises(HTTPException) as error:
        await internal.internal_initialize_payment(request, db)
    assert error.value.status_code == 422
    db.add.assert_not_called()


async def test_internal_retry_cannot_change_online_to_manual(monkeypatch):
    payment = row(payment_method="paystack")
    db = db_for(payment)
    monkeypatch.setattr(
        internal, "calculate_additional_charges", AsyncMock(return_value=([], 0))
    )
    with pytest.raises(HTTPException) as error:
        await internal.internal_initialize_payment(
            InternalInitializeRequest(
                purpose="community",
                payment_method="manual_transfer",
                amount=20000,
                reference=payment.reference,
                member_auth_id="member",
            ),
            db,
        )
    assert error.value.status_code == 409


async def test_receipt_submission_is_pending_review_not_paid_and_retry_is_safe():
    payment = row()
    db = db_for(payment)
    body = TransferReceipt(
        access_token=manual_transfer.transfer_token(payment.reference),
        external_reference="BANK-ONE",
        received_date=(utc_now() - timedelta(days=1)).date(),
    )
    saved = await manual_recording.submit_transfer(payment.reference, body, db)
    assert saved.status == "pending_review" and not saved.fulfilled
    assert payment.entitlement_applied_at is None
    await manual_recording.submit_transfer(payment.reference, body, db)
    db.commit.assert_awaited_once()
    assert "access_token" not in payment.payment_metadata["submitted_transfer"]
    assert "member_auth_id" not in saved.model_dump()


async def test_bad_capability_cannot_read_or_change_payment():
    db = db_for(row())
    with pytest.raises(HTTPException):
        await manual_recording.view_transfer(
            "PAY-TRANSFER", TransferAccess(access_token="0" * 64), db
        )
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [
        {"status": PaymentStatus.PAID},
        {"payment_method": "paystack"},
    ],
)
async def test_receipt_cannot_change_paid_or_online_payment(changes):
    payment = row(**changes)
    db = db_for(payment)
    with pytest.raises(HTTPException):
        await manual_recording.submit_transfer(
            payment.reference,
            TransferReceipt(
                access_token=manual_transfer.transfer_token(payment.reference),
                external_reference="BANK-ONE",
                received_date=utc_now().date(),
            ),
            db,
        )
    db.commit.assert_not_awaited()


async def test_admin_attach_receipt_preserves_paid_status_and_fulfillment(monkeypatch):
    payment = row(status=PaymentStatus.PAID, entitlement_applied_at=utc_now())
    db = db_for(payment)
    validator = AsyncMock()
    monkeypatch.setattr(manual_recording, "validate_receipt_media", validator)
    body = AttachPaymentReceipt(proof_media_id=uuid4())
    saved = await manual_recording.attach_receipt(
        payment.reference, body, make_admin_user(), db
    )
    assert saved.status == PaymentStatus.PAID and saved.entitlement_applied_at
    assert saved.proof_of_payment_media_id == body.proof_media_id
    validator.assert_awaited_once()
    with pytest.raises(HTTPException) as error:
        await manual_recording.attach_receipt(
            payment.reference,
            AttachPaymentReceipt(proof_media_id=uuid4()),
            make_admin_user(),
            db,
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize("purpose", list(PaymentPurpose))
async def test_admin_settles_every_purpose_using_existing_fulfillment(
    monkeypatch, purpose
):
    payment = row(purpose=purpose)
    db = db_for(payment)
    db.execute.side_effect = [result(None), result(payment), result(None)]
    apply = AsyncMock(return_value=payment)
    monkeypatch.setattr(_entitlement, "_mark_paid_and_apply", apply)
    await manual_transfer.settle_offline(db, payment, receipt(), make_admin_user())
    assert apply.await_args.kwargs["provider"] == "offline"
    assert apply.await_args.kwargs["provider_reference"] == "BANK-TEST-001"
    assert payment.payment_metadata["recorded_by_auth_id"]
    assert payment.payment_method == "bank_transfer"


@pytest.mark.parametrize(
    "changes,request_changes,code",
    [
        ({}, {"amount_kobo": 1}, 422),
        ({"payment_metadata": {"wallet_hold_id": "hold"}}, {}, 409),
        ({"status": PaymentStatus.FAILED}, {}, 409),
        ({}, {"received_at": utc_now() + timedelta(days=1)}, 422),
        ({"status": PaymentStatus.PAID}, {}, 409),
    ],
)
async def test_admin_rejects_mismatch_mixed_closed_future_or_paid(
    monkeypatch, changes, request_changes, code
):
    payment = row(**changes)
    db = db_for(payment)
    apply = AsyncMock()
    monkeypatch.setattr(_entitlement, "_mark_paid_and_apply", apply)
    with pytest.raises(HTTPException) as error:
        await manual_transfer.settle_offline(
            db, payment, receipt(**request_changes), make_admin_user()
        )
    assert error.value.status_code == code
    apply.assert_not_awaited()


async def test_admin_duplicate_bank_reference_or_booking_is_rejected(monkeypatch):
    apply = AsyncMock()
    monkeypatch.setattr(_entitlement, "_mark_paid_and_apply", apply)
    for booking in (False, True):
        payment = row(session_booking_id=uuid4() if booking else None)
        db = db_for(payment)
        db.execute.side_effect = (
            [result(None), result(payment), result(None), result(None), result(row())]
            if booking
            else [result(None), result(payment), result(row())]
        )
        with pytest.raises(HTTPException) as error:
            await manual_transfer.settle_offline(
                db, payment, receipt(), make_admin_user()
            )
        assert error.value.status_code == 409
    apply.assert_not_awaited()


async def test_exact_offline_retry_does_not_apply_entitlement_twice(monkeypatch):
    payment = row(
        status=PaymentStatus.PAID,
        provider="offline",
        provider_reference="BANK-TEST-001",
        payment_method="bank_transfer",
    )
    apply = AsyncMock()
    monkeypatch.setattr(_entitlement, "_mark_paid_and_apply", apply)
    assert (
        await manual_transfer.settle_offline(
            db_for(payment), payment, receipt(), make_admin_user()
        )
        is payment
    )
    apply.assert_not_awaited()


async def test_receipt_must_be_private_and_owned_by_payer_or_recording_admin(
    monkeypatch,
):
    admin = make_admin_user()
    payment = row()
    for owner, purpose, allowed in [
        (admin.user_id, "payment_proof", True),
        ("member", "payment_proof", True),
        ("stranger", "payment_proof", False),
        (admin.user_id, "gallery", False),
    ]:
        monkeypatch.setattr(
            manual_transfer,
            "internal_get",
            AsyncMock(
                return_value=SimpleNamespace(
                    status_code=200,
                    json=lambda: {
                        "uploaded_by": owner,
                        "metadata": {"purpose": purpose},
                    },
                )
            ),
        )
        if allowed:
            await manual_transfer.validate_receipt_media(uuid4(), payment, admin)
        else:
            with pytest.raises(HTTPException):
                await manual_transfer.validate_receipt_media(uuid4(), payment, admin)


def test_bank_reference_required_and_arbitrary_fields_rejected():
    for changes in ({"external_reference": None}, {"member_auth_id": "other"}):
        with pytest.raises(ValidationError):
            receipt(**changes)


async def test_bank_topup_initializes_a_payments_record_instead_of_orphan_request(
    monkeypatch,
):
    from services.wallet_service.models import PaymentMethod, TopupStatus
    from services.wallet_service.services import topup_service

    db = db_for(None)
    monkeypatch.setattr(
        topup_service,
        "get_wallet_by_auth_id",
        AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    initialize = AsyncMock(
        return_value=SimpleNamespace(
            status_code=200,
            json=lambda: {
                "reference": "TOP-TEST",
                "authorization_url": "/payments/transfer/TOP-TEST#token=test",
            },
        )
    )
    monkeypatch.setattr(topup_service, "internal_post", initialize)
    topup = await topup_service.initiate_topup(
        db,
        member_auth_id="member",
        bubbles_amount=25,
        payment_method=PaymentMethod.BANK_TRANSFER,
    )
    payload = initialize.await_args.kwargs["json"]
    assert payload["payment_method"] == "manual_transfer" and payload["amount"] == 2500
    assert topup.status == TopupStatus.PROCESSING
    assert topup.paystack_authorization_url.startswith("/payments/transfer/")


async def test_service_receipt_metadata_does_not_expose_file_url():
    from services.media_service.routers.payment_proofs import payment_proof_metadata

    media = SimpleNamespace(
        id=uuid4(),
        uploaded_by=uuid4(),
        metadata_info={"purpose": "payment_proof"},
        file_url="private-object",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=media))
    metadata = await payment_proof_metadata(media.id, db)
    assert "file_url" not in metadata
    assert metadata["uploaded_by"] == str(media.uploaded_by)
    media.metadata_info = {"purpose": "gallery"}
    with pytest.raises(HTTPException):
        await payment_proof_metadata(media.id, db)


def test_admin_and_service_routes_keep_required_authentication():
    from libs.auth.dependencies import require_admin, require_service_role
    from services.media_service.routers.payment_proofs import router as media_router

    for route in manual_recording.router.routes:
        if "/admin/" in route.path:
            assert require_admin in [dep.call for dep in route.dependant.dependencies]
    for route in media_router.routes:
        assert require_service_role in [
            dep.call for dep in route.dependant.dependencies
        ]


async def test_upload_stores_only_media_id_and_does_not_mark_paid(monkeypatch):
    import io

    from fastapi import UploadFile

    payment = row()
    db = db_for(payment)
    media_id = uuid4()
    client = AsyncMock()
    client.post.return_value = SimpleNamespace(
        status_code=200, json=lambda: {"id": str(media_id)}
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(manual_recording.httpx, "AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr(
        manual_recording, "_service_role_jwt", lambda name: "service-token"
    )
    saved = await manual_recording.upload_transfer_receipt(
        payment.reference,
        manual_transfer.transfer_token(payment.reference),
        UploadFile(io.BytesIO(b"receipt"), filename="receipt.pdf"),
        db,
    )
    assert saved.status == "pending" and not saved.fulfilled and saved.receipt_attached
    assert payment.proof_of_payment_media_id == media_id
    assert "file_url" not in saved.model_dump()
    assert client.post.await_args.kwargs["headers"] == {
        "Authorization": "Bearer service-token"
    }


async def test_zero_manual_checkout_settles_without_waiting_for_bank_credit(
    monkeypatch,
):
    db = db_for(None)
    monkeypatch.setattr(
        internal, "calculate_additional_charges", AsyncMock(return_value=([], 0))
    )

    async def settle(db, payment, **kwargs):
        payment.status = PaymentStatus.PAID
        return payment

    apply = AsyncMock(side_effect=settle)
    monkeypatch.setattr(internal, "_mark_paid_and_apply", apply)
    await internal.internal_initialize_payment(
        InternalInitializeRequest(
            purpose="guest_pass",
            payment_method="manual_transfer",
            amount=0,
            reference="GUEST-FREE",
            member_auth_id="guest:one",
        ),
        db,
    )
    apply.assert_awaited_once()
    assert db.add.call_args.args[0].status == PaymentStatus.PAID


async def test_guest_recovery_refreshes_transfer_deadline_without_changing_bill(
    monkeypatch,
):
    checkout = dict(
        reference="PAY-TRANSFER",
        authorization_url="/payments/transfer/PAY-TRANSFER#token=test",
        access_code="",
        amount_kobo=2100000,
        additional_charges=[{"label": "Original charge", "amount_kobo": 100000}],
    )
    payment = row(
        purpose=PaymentPurpose.GUEST_PASS,
        amount=21000,
        payment_metadata={
            "subtotal_kobo": 2000000,
            "reservation_expires_at": "2026-01-01T10:00:00Z",
            "internal_checkout": checkout,
        },
    )
    db = db_for(payment)
    monkeypatch.setattr(
        internal,
        "calculate_additional_charges",
        AsyncMock(
            return_value=([{"label": "New charge", "amount_kobo": 999999}], 999999)
        ),
    )
    response = await internal.internal_initialize_payment(
        InternalInitializeRequest(
            purpose="guest_pass",
            payment_method="manual_transfer",
            amount=20000,
            reference=payment.reference,
            member_auth_id="member",
            metadata={"booking_mode": "settlement", "reservation_expires_at": None},
        ),
        db,
    )
    assert response.amount_kobo == 2100000
    assert response.additional_charges == checkout["additional_charges"]
    assert payment.payment_metadata["reservation_expires_at"] is None
    assert payment.payment_metadata["booking_mode"] == "settlement"
