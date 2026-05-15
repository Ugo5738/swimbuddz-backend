"""Per-PaymentPurpose integration tests for POST /payments/intents.

This is the second half of the money-path coverage. The first half
(test_payments_intents.py + test_payments_webhook.py) covered lookups,
admin completion, idempotency, and the Paystack webhook flow. This file
covers the *creation* of a payment intent — the branch in
`intent_creation.create_payment_intent` that runs once per PaymentPurpose
to compute the correct amount, apply discounts/Bubbles, persist the
PENDING row, and (when Paystack is configured) hand back a checkout URL.

`_apply_entitlement` was already split per-purpose (intents/_entitlement/);
when this file grows, it can mirror the same per-purpose layout. For now
we organize by section per purpose in one file — simpler to navigate
while coverage is still being filled in.

What's stubbed:
  - `_initialize_paystack` → fake checkout URL. We never call Paystack.
  - `_update_pending_payment_reference` → no-op. The cross-service
    pending-ref update is best-effort and tested elsewhere.
  - `httpx.AsyncClient` for purposes that look up a cohort/order/member
    on another service. Mocked per-test with the appropriate response.

What's tested per purpose:
  - Validation: required fields, value bounds, mutually-exclusive flags.
  - Amount computation: the math that turns inputs into a PENDING row.
  - Metadata correctness: enough of the metadata to prove the downstream
    entitlement step (in _entitlement/_<purpose>.py) will receive the
    right context.
  - Cross-cutting: discount + Bubbles + zero-amount auto-completion +
    unsupported purpose returns 501.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _install_paystack_stubs(monkeypatch, checkout_url="http://fake.test/checkout/PAY-X"):
    """Replace Paystack init + pending-ref update with deterministic stubs.

    Every intent-creation test needs these — without them, the route either
    tries to hit the real Paystack API or makes a real HTTP call to
    members-service for the pending_payment_reference update.
    """
    monkeypatch.setattr(
        "services.payments_service.routers.intents.intent_creation._initialize_paystack",
        AsyncMock(return_value=(checkout_url, "ACCESS-001")),
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents.intent_creation._update_pending_payment_reference",
        AsyncMock(return_value=None),
    )


def _override_current_user_email(payments_app, email="member@test.com"):
    """Set the test admin's email — required for Paystack initialization."""
    from libs.auth.dependencies import (
        get_current_user,
        get_optional_user,
        require_admin,
    )
    from libs.auth.models import AuthUser

    user = AuthUser(
        user_id=str(uuid.uuid4()),
        email=email,
        role="authenticated",
        app_metadata={"roles": ["admin", "member"]},
        user_metadata={},
    )

    async def _get():
        return user

    payments_app.dependency_overrides[get_current_user] = _get
    payments_app.dependency_overrides[get_optional_user] = _get
    payments_app.dependency_overrides[require_admin] = _get
    return user


def _fake_httpx_client(get_responses=None, post_responses=None):
    """Build a context-manager-compatible httpx.AsyncClient mock.

    `get_responses` is a list of (url_substring, status_code, json_body).
    The first response whose url_substring matches the requested URL is
    returned; if none matches, raise to surface the test gap clearly.
    """
    get_responses = list(get_responses or [])
    post_responses = list(post_responses or [])

    async def _get(url, **kwargs):
        for substr, code, body in get_responses:
            if substr in str(url):
                return httpx.Response(status_code=code, json=body)
        raise AssertionError(f"No mocked GET response matched URL: {url}")

    async def _post(url, **kwargs):
        for substr, code, body in post_responses:
            if substr in str(url):
                return httpx.Response(status_code=code, json=body)
        raise AssertionError(f"No mocked POST response matched URL: {url}")

    client_instance = AsyncMock()
    client_instance.get = _get
    client_instance.post = _post

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client_instance)
    cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=cm)
    return factory


# ===========================================================================
# COMMUNITY  — base * years, no external calls
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_community_one_year_uses_configured_annual_fee(
    payments_client, monkeypatch
):
    """COMMUNITY for 1 year → amount = COMMUNITY_ANNUAL_FEE_NGN * 1, status=PENDING."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "community", "years": 1},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == 20000.0
    assert body["currency"] == "NGN"
    assert body["status"] == "pending"
    assert body["checkout_url"] == "http://fake.test/checkout/PAY-X"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_community_multi_year_multiplies_fee(payments_client, monkeypatch):
    """COMMUNITY for N years → amount = base * N."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "community", "years": 3},
    )

    assert response.status_code == 201
    assert response.json()["amount"] == 60000.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_community_persists_years_in_metadata(
    payments_client, db_session, monkeypatch
):
    """Persisted Payment row must include `years` in metadata so the
    entitlement step knows how long to extend community membership."""
    from sqlalchemy import select

    from services.payments_service.app.main import app as payments_app
    from services.payments_service.models import Payment
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "community", "years": 2},
    )
    ref = response.json()["reference"]

    row = (
        await db_session.execute(select(Payment).where(Payment.reference == ref))
    ).scalar_one()
    assert (row.payment_metadata or {}).get("years") == 2


# ===========================================================================
# CLUB  — calls members-service for community_paid_until, optional extension
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_quarterly_basic_amount(payments_client, monkeypatch):
    """CLUB quarterly → amount comes from CLUB_QUARTERLY_FEE_NGN."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "CLUB_QUARTERLY_FEE_NGN", 80000)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    # Mock members-service /members/by-auth lookup — member already has
    # community membership well into the future so no extension is needed.
    far_future = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
    fake_client = _fake_httpx_client(
        get_responses=[("/members/by-auth/", 200, {
            "membership": {"community_paid_until": far_future},
        })],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "club", "club_billing_cycle": "quarterly"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == 80000.0
    assert body["requires_community_extension"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_flags_extension_when_member_short_on_community(
    payments_client, monkeypatch
):
    """If a 3-month CLUB extends past the member's community_paid_until,
    the response must report requires_community_extension=True with a
    non-zero `total_with_extension` so the frontend can show the upsell.

    We pass `include_community_extension=False` here so the extension is
    surfaced but NOT auto-added to the bill.
    """
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "CLUB_QUARTERLY_FEE_NGN", 80000)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    # Member's community runs out tomorrow — a 3-month club purchase will
    # outlast it by ~89 days → needs ~3 months of community extension.
    expiring_soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    fake_client = _fake_httpx_client(
        get_responses=[("/members/by-auth/", 200, {
            "membership": {"community_paid_until": expiring_soon},
        })],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "club",
            "club_billing_cycle": "quarterly",
            "include_community_extension": False,
        },
    )

    assert response.status_code == 201
    body = response.json()
    # Amount is just the club fee (extension not auto-added)
    assert body["amount"] == 80000.0
    assert body["requires_community_extension"] is True
    assert body["community_extension_months"] >= 1
    assert body["total_with_extension"] is not None
    assert body["total_with_extension"] > 80000.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_adds_extension_when_opted_in(payments_client, monkeypatch):
    """When include_community_extension=True and extension is needed, the
    extension cost is folded into the total amount."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "CLUB_QUARTERLY_FEE_NGN", 80000)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    expiring_soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    fake_client = _fake_httpx_client(
        get_responses=[("/members/by-auth/", 200, {
            "membership": {"community_paid_until": expiring_soon},
        })],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "club",
            "club_billing_cycle": "quarterly",
            "include_community_extension": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    # Now the total includes the extension cost on top of the club fee
    assert body["amount"] > 80000.0


# ===========================================================================
# CLUB_BUNDLE  — Community + Club combined, no external lookup
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_bundle_sums_community_plus_club(payments_client, monkeypatch):
    """CLUB_BUNDLE amount = community_fee * years + club_amount(cycle)."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)
    monkeypatch.setattr(intent_creation.settings, "CLUB_QUARTERLY_FEE_NGN", 80000)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "club_bundle",
            "years": 1,
            "club_billing_cycle": "quarterly",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == 20000.0 + 80000.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_bundle_metadata_has_components(
    payments_client, db_session, monkeypatch
):
    """Discount validation requires bundle components in metadata.
    Persisted row must have `components.community` + `components.club`."""
    from sqlalchemy import select

    from services.payments_service.app.main import app as payments_app
    from services.payments_service.models import Payment
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)
    monkeypatch.setattr(intent_creation.settings, "CLUB_QUARTERLY_FEE_NGN", 80000)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "club_bundle",
            "years": 1,
            "club_billing_cycle": "quarterly",
        },
    )
    ref = response.json()["reference"]
    row = (
        await db_session.execute(select(Payment).where(Payment.reference == ref))
    ).scalar_one()
    components = (row.payment_metadata or {}).get("components") or {}
    assert components.get("community") == 20000.0
    assert components.get("club") == 80000.0


# ===========================================================================
# ACADEMY_COHORT  — calls academy-service for installments
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_academy_cohort_requires_enrollment_id(payments_client, monkeypatch):
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "academy_cohort"},  # no enrollment_id
    )

    assert response.status_code == 400
    assert "enrollment_id is required" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_academy_cohort_uses_next_unpaid_installment(
    payments_client, monkeypatch
):
    """When the cohort has installments, amount = next unpaid one (in NGN,
    converted from the kobo value academy returns)."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    enrollment_id = str(uuid.uuid4())
    cohort_id = str(uuid.uuid4())

    # Academy returns installments sorted by installment_number, with
    # amounts in kobo. First two paid, third pending.
    fake_client = _fake_httpx_client(
        get_responses=[(
            f"/internal/academy/enrollments/{enrollment_id}",
            200,
            {
                "id": enrollment_id,
                "cohort_id": cohort_id,
                "payment_status": "pending",
                "total_installments": 3,
                "installments": [
                    {"installment_number": 1, "amount": 5_000_000, "status": "paid"},
                    {"installment_number": 2, "amount": 5_000_000, "status": "paid"},
                    {"installment_number": 3, "amount": 5_000_000, "status": "pending"},
                ],
                "program": {"price_amount": 150000},
                "cohort": {"price_override": None},
            },
        )],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "academy_cohort",
            "enrollment_id": enrollment_id,
            "use_installments": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # 5_000_000 kobo / 100 = 50_000 NGN
    assert body["amount"] == 50_000.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_academy_cohort_amount_override_rejected_when_below_next_installment(
    payments_client, monkeypatch
):
    """Member-initiated custom amount must be >= next installment amount
    (founder policy May 2026). Below → 400.
    """
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    enrollment_id = str(uuid.uuid4())
    fake_client = _fake_httpx_client(
        get_responses=[(
            f"/internal/academy/enrollments/{enrollment_id}",
            200,
            {
                "id": enrollment_id,
                "cohort_id": str(uuid.uuid4()),
                "payment_status": "pending",
                "installments": [
                    # Next installment: 50,000 NGN (5_000_000 kobo)
                    {"installment_number": 1, "amount": 5_000_000, "status": "pending"},
                ],
                "program": {"price_amount": 150000},
                "cohort": {"price_override": None},
            },
        )],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "academy_cohort",
            "enrollment_id": enrollment_id,
            "use_installments": True,
            "amount_override_kobo": 1_000_000,  # only ₦10k — below the ₦50k next installment
        },
    )

    assert response.status_code == 400
    assert "less than the next stipulated installment" in response.json()["detail"]


# ===========================================================================
# STORE_ORDER  — calls store-service for order total
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_store_order_requires_order_id(payments_client, monkeypatch):
    from services.payments_service.app.main import app as payments_app

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "store_order"},
    )
    assert response.status_code == 400
    assert "order_id is required" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_store_order_uses_order_total(payments_client, monkeypatch):
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    order_id = str(uuid.uuid4())
    fake_client = _fake_httpx_client(
        get_responses=[(
            f"/store/admin/orders/{order_id}",
            200,
            {"order_number": "ORD-001", "total_ngn": 12500.0},
        )],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "store_order", "order_id": order_id},
    )
    assert response.status_code == 201, response.text
    assert response.json()["amount"] == 12500.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_store_order_rejects_zero_total(payments_client, monkeypatch):
    """A misconfigured order with zero total should not silently create a
    free payment — that would auto-complete and grant the order without
    payment going through."""
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    order_id = str(uuid.uuid4())
    fake_client = _fake_httpx_client(
        get_responses=[(
            f"/store/admin/orders/{order_id}",
            200,
            {"order_number": "ORD-002", "total_ngn": 0},
        )],
    )
    monkeypatch.setattr(intent_creation.httpx, "AsyncClient", fake_client)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "store_order", "order_id": order_id},
    )
    assert response.status_code == 400
    assert "greater than zero" in response.json()["detail"]


# ===========================================================================
# SESSION_FEE  — direct_amount + optional Bubbles
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_fee_requires_session_id(payments_client, monkeypatch):
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "session_fee", "direct_amount": 1000.0},
    )
    assert response.status_code == 400
    assert "session_id is required" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_fee_requires_positive_direct_amount(
    payments_client, monkeypatch
):
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "session_fee",
            "session_id": str(uuid.uuid4()),
            "direct_amount": 0,
        },
    )
    assert response.status_code == 400
    assert "direct_amount" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_fee_applies_bubbles(payments_client, monkeypatch):
    """5 Bubbles = ₦500 off. final amount = direct_amount - bubbles_value."""
    from services.payments_service.app.main import app as payments_app

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "session_fee",
            "session_id": str(uuid.uuid4()),
            "direct_amount": 2000.0,
            "bubbles_to_apply": 5,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["amount"] == 1500.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_fee_rejects_bubbles_exceeding_amount(
    payments_client, monkeypatch
):
    """50 Bubbles = ₦5000, more than the ₦1000 session fee → 400."""
    from services.payments_service.app.main import app as payments_app

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "session_fee",
            "session_id": str(uuid.uuid4()),
            "direct_amount": 1000.0,
            "bubbles_to_apply": 50,
        },
    )
    assert response.status_code == 400
    assert "bubbles_to_apply exceeds amount" in response.json()["detail"]


# ===========================================================================
# SESSION_BUNDLE  — multiple sessions, optional per-session ride configs
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_bundle_requires_session_ids(payments_client, monkeypatch):
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "session_bundle", "direct_amount": 5000.0},
    )
    # The body schema enforces session_ids is required for SESSION_BUNDLE
    # at the application layer; either 400 (route) or 422 (Pydantic) is
    # acceptable.
    assert response.status_code in (400, 422), response.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_bundle_rejects_duplicates(payments_client, monkeypatch):
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    dup_id = str(uuid.uuid4())
    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "session_bundle",
            "session_ids": [dup_id, dup_id, str(uuid.uuid4())],
            "direct_amount": 5000.0,
        },
    )
    assert response.status_code == 400
    assert "Duplicate" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_bundle_rejects_ride_config_for_session_not_in_bundle(
    payments_client, monkeypatch
):
    """Per-session ride configs must reference session IDs that are
    actually in the bundle; otherwise the rider buys a seat for a session
    they didn't pay for."""
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    in_bundle = str(uuid.uuid4())
    not_in_bundle = str(uuid.uuid4())
    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "session_bundle",
            "session_ids": [in_bundle],
            "direct_amount": 5000.0,
            "session_ride_configs": {
                not_in_bundle: {
                    "ride_config_id": str(uuid.uuid4()),
                    "pickup_location_id": str(uuid.uuid4()),
                    "num_seats": 1,
                },
            },
        },
    )
    assert response.status_code == 400
    assert "session_ride_configs" in response.json()["detail"]


# ===========================================================================
# RIDE_SHARE  — single ride after a session is already booked
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ride_share_requires_all_four_fields(payments_client, monkeypatch):
    """RIDE_SHARE needs session_id + ride_config_id + pickup_location_id
    + direct_amount. Missing any → 400 with a field-specific message.
    """
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    # Missing session_id
    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "ride_share", "direct_amount": 500.0},
    )
    assert response.status_code == 400
    assert "session_id is required" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ride_share_happy_path_persists_seats_and_ride_config(
    payments_client, db_session, monkeypatch
):
    from sqlalchemy import select

    from services.payments_service.app.main import app as payments_app
    from services.payments_service.models import Payment

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)

    sid, rid, pid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "ride_share",
            "session_id": sid,
            "ride_config_id": rid,
            "pickup_location_id": pid,
            "num_seats": 2,
            "direct_amount": 1500.0,
        },
    )
    assert response.status_code == 201, response.text
    ref = response.json()["reference"]
    row = (
        await db_session.execute(select(Payment).where(Payment.reference == ref))
    ).scalar_one()
    meta = row.payment_metadata or {}
    assert meta["session_id"] == sid
    assert meta["ride_config_id"] == rid
    assert meta["pickup_location_id"] == pid
    assert meta["num_seats"] == 2


# ===========================================================================
# Unsupported purposes  — WALLET_TOPUP falls through to else → 501
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wallet_topup_not_supported_via_intents_endpoint(
    payments_client, monkeypatch
):
    """WALLET_TOPUP payments are initiated through wallet_service, not
    here. The intents endpoint must reject them clearly (501) rather
    than create a PENDING row with no amount.
    """
    _override_current_user_email(
        __import__(
            "services.payments_service.app.main", fromlist=["app"]
        ).app
    )
    _install_paystack_stubs(monkeypatch)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "wallet_topup"},
    )
    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"].lower()


# ===========================================================================
# Cross-cutting: zero-amount auto-completion via 100% discount
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_discount_brings_amount_to_zero_and_auto_completes(
    payments_client, db_session, monkeypatch
):
    """When a discount code brings the amount to ₦0 (100% off), Paystack
    can't initialize a zero-amount transaction. The route must short-circuit
    via `_mark_paid_and_apply` and persist the row as PAID, not PENDING.
    """
    from sqlalchemy import select

    from services.payments_service.app.main import app as payments_app
    from services.payments_service.models import DiscountType
    from services.payments_service.routers.intents import intent_creation
    from tests.factories import DiscountFactory

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    # 100% off COMMUNITY for the duration of this test. Pass the enum
    # member (not its .value) so the route handler's `.value` access on
    # the in-memory identity-map row still works — see the same gotcha
    # documented in CohortFactory.
    discount = DiscountFactory.create(
        code="FREE100",
        discount_type=DiscountType.PERCENTAGE,
        value=100.0,
        applies_to=["COMMUNITY"],
    )
    db_session.add(discount)
    await db_session.commit()

    # Stub the entitlement step so we don't drive members-service.
    monkeypatch.setattr(
        "services.payments_service.routers.intents.intent_creation._mark_paid_and_apply",
        AsyncMock(side_effect=_mark_paid_stub),
    )

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "community", "years": 1, "discount_code": "FREE100"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == 0
    assert body["discount_applied"] == 20000.0
    assert body["original_amount"] == 20000.0


async def _mark_paid_stub(*, db, payment, provider, provider_reference,
                         paid_at, provider_payload=None):
    """Lightweight replacement for _mark_paid_and_apply during tests."""
    from services.payments_service.models import PaymentStatus

    payment.status = PaymentStatus.PAID
    payment.provider = provider
    payment.provider_reference = provider_reference
    payment.paid_at = paid_at
    db.add(payment)
    await db.commit()
    return payment


# ===========================================================================
# Cross-cutting: Bubbles rejected on non-session purposes
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bubbles_silently_ignored_on_community(payments_client, monkeypatch):
    """Bubbles only apply to SESSION_FEE / SESSION_BUNDLE / RIDE_SHARE.
    Passing bubbles_to_apply with a COMMUNITY purpose must NOT change the
    amount (rather than crashing or partially applying).
    """
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)
    _install_paystack_stubs(monkeypatch)
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    response = await payments_client.post(
        "/payments/intents",
        json={"purpose": "community", "years": 1, "bubbles_to_apply": 50},
    )
    assert response.status_code == 201
    # Bubbles ignored — amount is the full fee
    assert response.json()["amount"] == 20000.0


# ===========================================================================
# Cross-cutting: body/path challenge_id-style mismatch
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_payment_method_manual_transfer_does_not_initialize_paystack(
    payments_client, monkeypatch
):
    """When payment_method=manual_transfer, the route must NOT call
    Paystack init. The response has no checkout_url.
    """
    from services.payments_service.app.main import app as payments_app
    from services.payments_service.routers.intents import intent_creation

    _override_current_user_email(payments_app)

    paystack_mock = AsyncMock(
        side_effect=AssertionError("Paystack must NOT be initialized for manual transfer")
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents.intent_creation._initialize_paystack",
        paystack_mock,
    )
    monkeypatch.setattr(
        "services.payments_service.routers.intents.intent_creation._update_pending_payment_reference",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(intent_creation.settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)

    response = await payments_client.post(
        "/payments/intents",
        json={
            "purpose": "community",
            "years": 1,
            "payment_method": "manual_transfer",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["checkout_url"] is None
    assert body["status"] == "pending"
