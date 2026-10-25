"""A combined receipt settles exact historical charges without double collection."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers import session_settlements as routes
from services.payments_service.routers.intents import admin
from services.payments_service.routers.intents._entitlement import _dispatcher
from services.payments_service.schemas import CreatePaymentIntentRequest
from services.payments_service.schemas.session_settlement import (
    AdminSessionSettlementRequest,
)
from services.payments_service.services import session_settlement as service
from services.payments_service.services.booking_payment_attempts import (
    existing_booking_attempt,
)
from services.payments_service.services.ledger_emit import build_post_kwargs
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionParticipant,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def charges(db_session, sessions_client, monkeypatch):
    member_id, auth_id = uuid4(), str(uuid4())
    bookings, sessions = [], []
    for offset in (9, 7, 2, 0):
        start = utc_now() - timedelta(days=offset, hours=3)
        session = Session(
            id=uuid4(),
            title="Extra class",
            session_type="cohort_class",
            cohort_id=uuid4(),
            cohort_fee_mode="paid_extra",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            pool_fee=1_500_000,
        )
        db_session.add(session)
        booking = SessionBooking(
            id=uuid4(),
            session_id=session.id,
            member_id=member_id,
            member_auth_id=auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel="admin",
            fee_amount_kobo=1_500_000,
            member_fee_amount_kobo=1_500_000,
            party_size=1,
            booking_source="admin_walk_in",
        )
        db_session.add(booking)
        sessions.append(session)
        bookings.append(booking)
    old_payment = Payment(
        id=uuid4(),
        reference=f"existing-{uuid4()}",
        member_auth_id=auth_id,
        purpose=PaymentPurpose.SESSION_BOOKING,
        amount=15000,
        currency="NGN",
        status=PaymentStatus.PAID,
        paid_at=utc_now() - timedelta(days=6),
        entitlement_applied_at=utc_now() - timedelta(days=6),
        session_booking_id=bookings[1].id,
        payment_metadata={"booking_id": str(bookings[1].id)},
    )
    db_session.add(old_payment)
    bookings[1].payment_intent_id = old_payment.id
    guest = SessionParticipant(
        id=uuid4(),
        session_id=sessions[1].id,
        participant_kind="guest",
        source="walk_in",
        full_name_snapshot="Toyosi",
        fee_amount_kobo=1_000_000,
        payment_status="unpaid",
        waiver_status="missing",
    )
    db_session.add(guest)
    await db_session.commit()

    async def request(*, path, json, **kwargs):
        return await sessions_client.post(path, json=json)

    monkeypatch.setattr(service, "internal_post", request)
    monkeypatch.setattr(routes, "_paystack_enabled", lambda: True)
    initialize = AsyncMock(
        return_value=("https://checkout.example.test/combined", "combined")
    )
    monkeypatch.setattr(routes, "_initialize_paystack", initialize)
    monkeypatch.setattr(
        routes,
        "get_member_by_auth_id",
        AsyncMock(return_value={"email": "ajibola@example.test"}),
    )
    for name in (
        "_clear_pending_tier_payment_for_payment",
        "_send_membership_activation_email",
        "_try_qualify_referral",
        "_emit_membership_reward_events",
        "_dispatch_payment_notification",
    ):
        monkeypatch.setattr(_dispatcher, name, AsyncMock())
    from services.payments_service.services import ledger_emit

    ledger = AsyncMock()
    monkeypatch.setattr(ledger_emit, "emit_payment_to_ledger", ledger)
    payload = AdminSessionSettlementRequest(
        member_auth_id=auth_id,
        booking_ids=[bookings[i].id for i in (0, 2, 3)],
        guest_participant_ids=[guest.id],
        expected_total_kobo=5_500_000,
        idempotency_key=uuid4(),
        note="User confirmed these four attended charges",
    )
    return payload, bookings, guest, old_payment, initialize, ledger


async def issue(charges, db_session):
    payload = charges[0]
    return await routes.create_session_settlement(
        payload, AuthUser(user_id="admin", role="service_role"), db_session
    )


async def test_combined_checkout_is_frozen_and_retry_reuses_one_provider_link(
    charges, db_session
):
    first = await issue(charges, db_session)
    second = await issue(charges, db_session)
    assert first == second
    assert first["amount"] == 55000
    assert sum(line["amount_kobo"] for line in first["lines"]) == 5_500_000
    assert len(first["lines"]) == 4
    charges[4].assert_awaited_once()
    for index in (0, 2, 3):
        await db_session.refresh(charges[1][index])
        assert charges[1][index].payment_intent_id is None
    await db_session.refresh(charges[2])
    assert charges[2].payment_status == "unpaid"


async def test_paid_receipt_settles_every_line_and_preserves_prior_payment(
    charges, db_session
):
    result = await issue(charges, db_session)
    payment = (
        await db_session.execute(
            select(Payment).where(Payment.reference == result["reference"])
        )
    ).scalar_one()
    for _ in range(2):
        await _dispatcher._mark_paid_and_apply(
            db_session, payment, "paystack", payment.reference, utc_now()
        )
    assert payment.entitlement_applied_at is not None
    assert payment.entitlement_error is None
    for index in (0, 2, 3):
        await db_session.refresh(charges[1][index])
        assert charges[1][index].payment_intent_id == payment.id
    await db_session.refresh(charges[1][1])
    assert charges[1][1].payment_intent_id == charges[3].id
    await db_session.refresh(charges[2])
    assert charges[2].payment_status == "paid"
    assert charges[2].payment_reference == payment.reference
    assert charges[2].fee_amount_kobo == 1_000_000
    assert charges[2].waiver_status == "missing"
    charges[5].assert_awaited_once()
    journal = build_post_kwargs(payment)
    assert journal["lines"][1]["account_ref"] == "revenue_academy"
    assert journal["lines"][1]["credit"] == 5_500_000
    await service.apply_session_settlement(
        payment
    )  # downstream retry is also idempotent
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SessionParticipant)
            .where(
                SessionParticipant.session_id.in_(
                    [row.session_id for row in charges[1]]
                )
            )
        )
        == 5
    )  # four member identities plus the one guest


async def test_another_combined_link_cannot_collect_any_of_the_same_charges(
    charges, db_session
):
    await issue(charges, db_session)
    charges[0].idempotency_key = uuid4()
    with pytest.raises(HTTPException, match="already has a payment"):
        await issue(charges, db_session)
    charges[4].assert_awaited_once()


async def test_old_individual_billing_and_guest_links_cannot_collect_again(
    charges, db_session
):
    await issue(charges, db_session)
    payload, bookings, guest, *_ = charges
    with pytest.raises(HTTPException, match="combined payment"):
        await existing_booking_attempt(
            db_session,
            bookings[0].id,
            payload.member_auth_id,
            CreatePaymentIntentRequest(purpose="session_booking"),
        )
    with pytest.raises(HTTPException, match="combined payment"):
        await admin._existing_participant_payment(db_session, guest.id)


async def test_already_paid_swim_cannot_be_added_to_combined_charge(
    charges, db_session
):
    charges[0].booking_ids.append(charges[1][1].id)
    with pytest.raises(HTTPException, match="already has a payment"):
        await issue(charges, db_session)
    charges[4].assert_not_awaited()


async def test_total_mismatch_does_not_create_a_provider_charge(charges, db_session):
    charges[0].expected_total_kobo = 4_000_000
    with pytest.raises(HTTPException, match="expected total"):
        await issue(charges, db_session)
    charges[4].assert_not_awaited()


async def test_changed_line_rejects_fulfillment_atomically(charges, db_session):
    result = await issue(charges, db_session)
    payment = (
        await db_session.execute(
            select(Payment).where(Payment.reference == result["reference"])
        )
    ).scalar_one()
    charges[2].fee_amount_kobo = 1_100_000
    await db_session.commit()
    await _dispatcher._mark_paid_and_apply(
        db_session, payment, "paystack", payment.reference, utc_now()
    )
    assert payment.status == PaymentStatus.PAID
    assert payment.entitlement_applied_at is None
    assert "differ" in payment.entitlement_error
    for index in (0, 2, 3):
        await db_session.refresh(charges[1][index])
        assert charges[1][index].payment_intent_id is None
    assert charges[2].payment_status == "unpaid"


async def test_checkout_key_cannot_be_reused_for_different_charges(charges, db_session):
    await issue(charges, db_session)
    charges[0].guest_participant_ids = []
    with pytest.raises(HTTPException, match="different session charges"):
        await issue(charges, db_session)


async def test_guest_cannot_be_collected_in_two_disjoint_member_bundles(
    charges, db_session
):
    original = charges[0]
    first_booking = original.booking_ids[0]
    original.booking_ids = [first_booking]
    original.expected_total_kobo = 2_500_000
    await issue(charges, db_session)
    original.idempotency_key = uuid4()
    original.booking_ids = [charges[1][2].id]
    with pytest.raises(HTTPException, match="guest already has"):
        await issue(charges, db_session)


async def test_member_cannot_inject_admin_settlement_metadata():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="issued by Admin"):
        CreatePaymentIntentRequest(
            purpose="session_bundle", metadata={service.METADATA_KEY: {"total_kobo": 1}}
        )


async def test_unknown_provider_outcome_retains_one_frozen_payment(
    charges, db_session, monkeypatch
):
    initialize = AsyncMock(side_effect=HTTPException(502, "Provider request timed out"))
    monkeypatch.setattr(routes, "_initialize_paystack", initialize)
    with pytest.raises(HTTPException, match="timed out"):
        await issue(charges, db_session)
    with pytest.raises(HTTPException, match="still being prepared"):
        await issue(charges, db_session)
    initialize.assert_awaited_once()


async def test_regular_member_cannot_issue_combined_payment():
    import httpx
    from fastapi import FastAPI
    from libs.auth.dependencies import get_current_user

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="member", role="authenticated", app_metadata={"roles": ["member"]}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/payments/admin/session-settlements", json={})
    assert response.status_code == 403
