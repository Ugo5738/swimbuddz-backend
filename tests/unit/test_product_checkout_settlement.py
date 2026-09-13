from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from services.payments_service.models import PaymentPurpose, PaymentStatus
from services.payments_service.schemas import (
    InternalInitializeRequest,
    CreatePaymentIntentRequest,
)
from services.payments_service.services import experience_checkout as checkout
from services.payments_service.services import product_intent_retry as retry
from services.payments_service.services.refund_tenders import academy_refund_tenders
from services.payments_service.routers.intents._entitlement import (
    _dispatcher,
    _academy_cohort,
    _community_experience,
)
from services.members_service.routers.experience_tickets import ticket_payment_request
from services.members_service.schemas.experience import ExperienceOrderCheckout


def quote(**changes):
    return {
        "subtotal_kobo": 5000000,
        "net_subtotal_kobo": 4500000,
        "discount_kobo": 500000,
        "discount_code": "TRIP",
        "bubbles_to_apply": 50,
        "bubbles_value_kobo": 500000,
        "total_kobo": 4000000,
        "additional_charges": [],
        "additional_charges_total_kobo": 0,
        **changes,
    }


def request(**changes):
    return InternalInitializeRequest(
        purpose="community_experience",
        reference="EXPERIENCE-ONE",
        amount=50000,
        currency="NGN",
        member_auth_id="member",
        discount_code="TRIP",
        bubbles_to_apply=50,
        metadata={
            "experience_order_id": "order",
            "experience_order_amount_kobo": 5000000,
            "checkout_components_kobo": {"community_experience": 5000000},
            "payer_email": "test@example.com",
        },
        **changes,
    )


def payment(**changes):
    return SimpleNamespace(
        id=uuid4(),
        reference="EXPERIENCE-ONE",
        amount=40000,
        currency="NGN",
        member_auth_id="member",
        purpose=PaymentPurpose.COMMUNITY_EXPERIENCE,
        payment_method="paystack",
        created_at=datetime.now(timezone.utc),
        payment_metadata={"experience_order_id": "order", "checkout_quote": quote()},
        entitlement_applied_at=None,
        **changes,
    )


def database(existing=None):
    return SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: existing)
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
        add=MagicMock(),
    )


@pytest.mark.asyncio
async def test_experience_preview_resumes_frozen_price_without_repricing(monkeypatch):
    old = payment(status=PaymentStatus.PENDING)
    price = AsyncMock()
    monkeypatch.setattr(checkout, "price_product_checkout", price)
    assert await checkout.preview_experience_checkout(request(), database(old)) == {
        **old.payment_metadata["checkout_quote"],
        "selection_locked": True,
    }
    price.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"discount_code": "OTHER"},
        {"bubbles_to_apply": 10},
        {"member_auth_id": "intruder"},
    ],
)
def test_experience_frozen_payment_rejects_changed_choices_or_owner(change):
    req = request().model_copy(update=change)
    with pytest.raises(HTTPException):
        checkout.frozen_quote(payment(status=PaymentStatus.PENDING), req)


@pytest.mark.asyncio
async def test_paid_experience_retry_never_reserves_or_charges_again(monkeypatch):
    old = payment(status=PaymentStatus.PAID)
    old.entitlement_applied_at = datetime.now(timezone.utc)
    hold = AsyncMock()
    monkeypatch.setattr(checkout, "create_wallet_hold", hold)
    result = await checkout.initialize_experience_checkout(request(), database(old))
    assert result.confirmed
    hold.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_wallet_experience_has_no_provider_initialization(monkeypatch):
    from services.payments_service.routers.intents import _entitlement, _paystack

    db = database()
    q = quote(
        discount_code=None,
        discount_kobo=0,
        net_subtotal_kobo=5000000,
        bubbles_to_apply=500,
        bubbles_value_kobo=5000000,
        total_kobo=0,
    )
    monkeypatch.setattr(
        checkout, "preview_experience_checkout", AsyncMock(return_value=q)
    )
    hold = AsyncMock(return_value={"id": "hold"})
    monkeypatch.setattr(checkout, "create_wallet_hold", hold)

    async def settle(**kw):
        p = kw["payment"]
        p.entitlement_applied_at = datetime.now(timezone.utc)
        return p

    monkeypatch.setattr(_entitlement, "_mark_paid_and_apply", settle)
    provider = AsyncMock()
    monkeypatch.setattr(_paystack, "_initialize_paystack", provider)
    req = request().model_copy(update={"discount_code": None, "bubbles_to_apply": 500})
    result = await checkout.initialize_experience_checkout(req, db)
    assert result.confirmed and result.amount_kobo == 0
    hold.assert_awaited_once()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_experience_provider_response_retains_one_reference_and_hold(
    monkeypatch,
):
    from services.payments_service.routers.intents import _paystack

    db = database()
    monkeypatch.setattr(
        checkout, "preview_experience_checkout", AsyncMock(return_value=quote())
    )
    monkeypatch.setattr(_paystack, "_paystack_enabled", lambda: True)
    provider = AsyncMock(side_effect=HTTPException(502, "Timed out"))
    monkeypatch.setattr(_paystack, "_initialize_paystack", provider)
    hold, release = AsyncMock(return_value={"id": "hold"}), AsyncMock()
    monkeypatch.setattr(checkout, "create_wallet_hold", hold)
    monkeypatch.setattr(checkout, "release_wallet_hold", release)
    with pytest.raises(HTTPException):
        await checkout.initialize_experience_checkout(request(), db)
    saved = db.add.call_args.args[0]
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: saved)
    provider.side_effect = None
    provider.return_value = ("https://checkout.paystack.com/test", "code")
    result = await checkout.initialize_experience_checkout(request(), db)
    assert result.reference == "EXPERIENCE-ONE"
    assert saved.status == PaymentStatus.PENDING
    hold.assert_awaited_once()
    release.assert_not_awaited()


def test_order_access_token_is_not_authority_to_spend_members_wallet():
    body = ExperienceOrderCheckout(access_token="secret" * 8, bubbles_to_apply=1)
    order = SimpleNamespace(member_auth_id="owner")
    for user in (None, SimpleNamespace(user_id="intruder")):
        with pytest.raises(HTTPException) as exc:
            ticket_payment_request(order, body, user)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_product_wallet_capture_failure_blocks_entitlement(monkeypatch):
    p = payment(status=PaymentStatus.PAID)
    p.payment_metadata["bubbles_to_apply"] = 50
    capture = AsyncMock(side_effect=HTTPException(502, "Cannot capture"))
    handler = AsyncMock()
    monkeypatch.setattr(_dispatcher, "_debit_bubbles", capture)
    monkeypatch.setitem(_dispatcher._PURPOSE_HANDLERS, p.purpose, handler)
    with pytest.raises(HTTPException):
        await _dispatcher._apply_entitlement(p)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_product_wallet_missing_confirmation_blocks_entitlement(monkeypatch):
    p = payment(status=PaymentStatus.PAID)
    p.payment_metadata["bubbles_to_apply"] = 50
    handler = AsyncMock()
    monkeypatch.setattr(_dispatcher, "_debit_bubbles", AsyncMock(return_value=None))
    monkeypatch.setitem(_dispatcher._PURPOSE_HANDLERS, p.purpose, handler)
    with pytest.raises(HTTPException, match="Wallet settlement was not confirmed"):
        await _dispatcher._apply_entitlement(p)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_wallet_funded_academy_installment_does_not_clear_future_obligations(
    monkeypatch,
):
    p = payment(status=PaymentStatus.PAID)
    p.amount, p.paid_at = 0, None
    p.payment_metadata = {
        "enrollment_id": "student",
        "academy_payment_amount_kobo": 6000000,
        "installment_number": 1,
    }
    post = AsyncMock(return_value=httpx.Response(200))
    client = MagicMock()
    client.__aenter__.return_value = SimpleNamespace(post=post)
    monkeypatch.setattr(_academy_cohort.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(_academy_cohort, "_service_role_jwt", lambda *_: "jwt")
    await _academy_cohort.apply_academy_cohort(p)
    assert post.await_args.kwargs["json"]["amount_kobo"] == 6000000
    assert "clear_installments" not in post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_experience_confirmation_reconciles_cash_wallet_discount(monkeypatch):
    from libs.common import service_client

    p = payment(status=PaymentStatus.PAID)
    p.payment_metadata["experience_order_amount_kobo"] = 5000000
    confirm = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(service_client, "internal_post", confirm)
    await _community_experience.apply_community_experience(p)
    assert confirm.await_args.kwargs["json"]["amount_kobo"] == 5000000
    p.amount = 39999
    with pytest.raises(HTTPException):
        await _community_experience.apply_community_experience(p)


def test_refund_excludes_discount_and_membership_and_preserves_tender():
    meta = {
        "checkout_quote": quote(
            net_components_kobo={"academy_cohort": 4000000, "community": 2000000},
            discount_allocations_kobo={"academy_cohort": 1000000},
            net_subtotal_kobo=6000000,
            bubbles_value_kobo=3000000,
        )
    }
    refund = academy_refund_tenders(meta, 5000000)
    assert refund["refund_kobo"] == 2000000
    assert refund["refund_bubbles"] == 200
    assert refund["discount_excluded_kobo"] == 1000000
    assert refund["net_refund_kobo"] == 4000000


def test_fractional_wallet_refund_is_not_silently_cashed_out():
    meta = {
        "checkout_quote": quote(
            net_components_kobo={"academy_cohort": 10000},
            discount_allocations_kobo={},
            net_subtotal_kobo=10000,
            bubbles_value_kobo=10000,
        )
    }
    refund = academy_refund_tenders(meta, 5000)
    assert refund["refund_kobo"] == 0
    assert refund["refund_bubbles_remainder_kobo"] == 5000


def test_member_retry_fingerprint_freezes_selection_not_display_refresh():
    req = CreatePaymentIntentRequest(
        purpose="community", idempotency_key=uuid4(), expected_total_kobo=2000000
    )
    assert retry.request_fingerprint(req) == retry.request_fingerprint(
        req.model_copy(update={"expected_total_kobo": 2010000})
    )
    assert retry.request_fingerprint(req) != retry.request_fingerprint(
        req.model_copy(update={"bubbles_to_apply": 1})
    )


@pytest.mark.asyncio
async def test_member_activation_drops_forged_wallet_and_price_metadata(monkeypatch):
    from services.payments_service.routers.intents import intent_creation as intents

    db = database()

    async def refresh(p):
        p.created_at = datetime.now(timezone.utc)

    db.refresh.side_effect = refresh
    hold = AsyncMock(return_value={"id": "real-hold"})
    monkeypatch.setattr(intents, "create_wallet_hold", hold)
    monkeypatch.setattr(intents.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    async def settle(**kw):
        p = kw["payment"]
        assert "wallet_transaction_id" not in p.payment_metadata
        p.status, p.entitlement_applied_at = (
            PaymentStatus.PAID,
            datetime.now(timezone.utc),
        )
        return p

    monkeypatch.setattr(intents, "_mark_paid_and_apply", settle)
    result = await intents.create_payment_intent(
        CreatePaymentIntentRequest(
            purpose="community",
            bubbles_to_apply=200,
            payment_metadata={
                "wallet_transaction_id": "forged",
                "components_kobo": {"club": 2000000},
                "checkout_quote": {"total_kobo": 0},
            },
        ),
        SimpleNamespace(user_id="member", email="test@example.com"),
        db,
    )
    assert result.amount == 0
    assert result.checkout_quote["net_components_kobo"] == {"community": 2000000}
    assert result.checkout_quote["discount_kobo"] == 0
    assert hold.await_args.kwargs["amount"] == 200


@pytest.mark.asyncio
async def test_insufficient_wallet_fails_before_creating_a_product_payment(monkeypatch):
    from services.payments_service.routers.intents import intent_creation as intents

    db = database()
    response = httpx.Response(
        402,
        json={"detail": "Insufficient available Bubbles"},
        request=httpx.Request("POST", "http://wallet/holds"),
    )
    monkeypatch.setattr(
        intents,
        "create_wallet_hold",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "insufficient", request=response.request, response=response
            )
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await intents.create_payment_intent(
            CreatePaymentIntentRequest(purpose="community", bubbles_to_apply=200),
            SimpleNamespace(user_id="member", email="test@example.com"),
            db,
        )
    assert exc.value.status_code == 402
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_wallet_refund_is_idempotent_and_never_becomes_cash(monkeypatch):
    from libs.common import service_client
    from services.payments_service.models import Payment
    from services.payments_service.routers import manual
    from services.payments_service.schemas import MarkRefundDisbursedRequest
    from services.payments_service.services import ledger_emit

    p = Payment(
        id=uuid4(),
        reference="PAY-REFUND",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        member_auth_id="member",
        amount=0,
        payment_metadata={
            "wallet_transaction_id": "captured",
            "refund_owed": [
                {
                    "enrollment_id": "student",
                    "refund_kobo": 0,
                    "refund_bubbles": 100,
                    "disbursed_at": None,
                }
            ],
        },
    )
    db = database(p)
    credit = AsyncMock(return_value={"success": True, "transaction_id": "refund-txn"})
    emit = AsyncMock()
    monkeypatch.setattr(service_client, "credit_member_wallet", credit)
    monkeypatch.setattr(ledger_emit, "emit_refund_disbursed_to_ledger", emit)
    for _ in range(2):
        await manual.mark_refund_disbursed(
            "PAY-REFUND",
            MarkRefundDisbursedRequest(enrollment_id="student"),
            SimpleNamespace(email="admin@example.com"),
            db,
        )
    credit.assert_awaited_once()
    assert credit.await_args.kwargs["amount"] == 100
    assert (
        credit.await_args.kwargs["idempotency_key"]
        == "academy-refund:PAY-REFUND:student"
    )
    assert emit.await_args.args[2] == 0


@pytest.mark.asyncio
async def test_fractional_wallet_refund_cannot_be_marked_disbursed(monkeypatch):
    from libs.common import service_client
    from services.payments_service.models import Payment
    from services.payments_service.routers import manual
    from services.payments_service.schemas import MarkRefundDisbursedRequest

    p = Payment(
        reference="PAY-REFUND",
        payment_metadata={
            "refund_owed": [
                {
                    "enrollment_id": "student",
                    "refund_kobo": 0,
                    "refund_bubbles_remainder_kobo": 5000,
                }
            ]
        },
    )
    credit = AsyncMock()
    monkeypatch.setattr(service_client, "credit_member_wallet", credit)
    with pytest.raises(HTTPException, match="fractional Bubbles"):
        await manual.mark_refund_disbursed(
            "PAY-REFUND",
            MarkRefundDisbursedRequest(enrollment_id="student"),
            SimpleNamespace(email="admin@example.com"),
            database(p),
        )
    credit.assert_not_awaited()
