from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.communications_service.templates.payment_details import checkout_details
from services.payments_service.models import PaymentPurpose
from services.payments_service.services.ledger_emit import build_post_kwargs
from services.wallet_service.models import TransactionType
from services.wallet_service.services.ledger_emit import build_wallet_post_kwargs
from services.payments_service.services.product_intent_retry import (
    find_product_retry,
    request_fingerprint,
)
from services.payments_service.schemas import CreatePaymentIntentRequest
from services.payments_service.services.experience_checkout import frozen_quote


def test_receipt_distinguishes_discount_wallet_and_cash():
    details = checkout_details(
        {
            "subtotal_kobo": 2000000,
            "discount_kobo": 200000,
            "bubbles_to_apply": 180,
            "total_kobo": 0,
            "additional_charges_total_kobo": 0,
        }
    )
    assert details == {
        "Original price": "NGN 20,000.00",
        "Discount": "−NGN 2,000.00",
        "Paid with Bubbles": "180",
        "Paid in cash": "NGN 0.00",
    }


def test_wallet_only_payment_has_no_cash_journal():
    p = SimpleNamespace(purpose=PaymentPurpose.COMMUNITY, amount=0, provider="internal")
    assert build_post_kwargs(p) is None


@pytest.mark.parametrize(
    "purpose,account",
    [
        ("community", "deferred_revenue_community"),
        ("club", "deferred_revenue_club"),
        ("club_bundle", "deferred_revenue_club"),
        ("academy_cohort", "deferred_revenue_academy"),
        ("community_experience", "deferred_revenue_community"),
    ],
)
def test_product_wallet_settlement_posts_to_product_revenue(purpose, account):
    txn = SimpleNamespace(
        id=uuid4(),
        transaction_type=TransactionType.PURCHASE,
        amount=20,
        txn_metadata={},
        reference_type=purpose,
        service_source="payments",
        reference_id="PAY-ONE",
        created_at=datetime.now(timezone.utc),
    )
    journal = build_wallet_post_kwargs(txn, "member")
    assert sum(line["debit"] for line in journal["lines"]) == 200000
    assert sum(line["credit"] for line in journal["lines"]) == 200000
    assert journal["lines"][-1]["account_ref"] == account


@pytest.mark.asyncio
async def test_product_key_is_member_scoped_and_keeps_pay_prefix():
    req = CreatePaymentIntentRequest(purpose="community", idempotency_key=uuid4())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    )
    first, _ = await find_product_retry(db, req, "ay")
    same, _ = await find_product_retry(db, req, "ay")
    other, _ = await find_product_retry(db, req, "uche")
    assert first.startswith("PAY-") and first == same and first != other
    saved = SimpleNamespace(
        member_auth_id="ay",
        payment_metadata={"request_fingerprint": request_fingerprint(req)},
    )
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: saved)
    assert (await find_product_retry(db, req, "ay"))[1] is saved


def test_existing_cash_experience_is_resumable_but_modifiers_are_locked():
    req = SimpleNamespace(
        member_auth_id="ay",
        payment_method="paystack",
        currency="NGN",
        discount_code=None,
        bubbles_to_apply=0,
        metadata={
            "experience_order_id": "order",
            "checkout_components_kobo": {"community_experience": 5000000},
        },
    )
    payment = SimpleNamespace(
        member_auth_id="ay",
        payment_method="paystack",
        currency="NGN",
        amount=50100,
        payment_metadata={
            "experience_order_id": "order",
            "experience_order_amount_kobo": 5000000,
            "additional_charges_total_kobo": 10000,
        },
    )
    quote = frozen_quote(payment, req)
    assert quote["total_kobo"] == 5010000
    assert quote["bubbles_to_apply"] == 0
    assert quote["selection_locked"] is True
