"""Isolated PostgreSQL flow/concurrency tests.

Run only against a migrated disposable localhost database:
GUEST_TEST_DATABASE_URL=postgresql+psycopg://... pytest .../test_guest_booking_database.py
All payment, member, rewards and email HTTP calls are mocked.
"""

import asyncio
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    BookingEmailDelivery,
    GuestBookingGrant,
    GuestPass,
    Session,
    SessionBooking,
    SessionStatus,
    SessionType,
)
from services.sessions_service.routers import (
    guest_booking_admin,
    guest_passes,
    internal,
)
from services.sessions_service.schemas import BookingConfirmRequest
from services.sessions_service.schemas.guest_pass import (
    GuestBookingGrantCreate,
    GuestPassAttendanceUpdate,
    GuestPassConfirm,
    GuestPassCreate,
)
from services.sessions_service.services import (
    booking_confirmation,
    guest_booking,
    guest_checkout,
)
from services.sessions_service.services.booking_capacity import assert_booking_capacity


@pytest_asyncio.fixture
async def guest_env(monkeypatch):
    url = os.environ.get("GUEST_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set GUEST_TEST_DATABASE_URL to a migrated disposable localhost database"
        )
    parsed = make_url(url)
    assert parsed.host in {
        "localhost",
        "127.0.0.1",
    }, "Only a disposable localhost database may be used"
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        booking_confirmation,
        "get_email_client",
        lambda: SimpleNamespace(send_template=sender),
    )
    monkeypatch.setattr(
        booking_confirmation,
        "get_member_by_auth_id",
        AsyncMock(
            return_value={
                "first_name": "Ada",
                "last_name": "Member",
                "email": "member@example.com",
            }
        ),
    )
    monkeypatch.setattr(
        booking_confirmation,
        "member_guest_url",
        AsyncMock(return_value="https://swimbuddz.test/guest?ref=ADA"),
    )
    monkeypatch.setattr(internal, "sync_booking_attendance", AsyncMock())
    provider = AsyncMock(
        return_value=httpx.Response(
            200,
            request=httpx.Request("POST", "https://payments.test/initialize"),
            json={
                "authorization_url": "https://checkout.test/pay",
                "amount_kobo": 500000,
                "additional_charges": [],
            },
        )
    )
    monkeypatch.setattr(guest_checkout, "internal_post", provider)
    monkeypatch.setattr(
        guest_passes,
        "_resolve_referrer_auth_id",
        AsyncMock(return_value="referrer-auth"),
    )
    monkeypatch.setattr(
        guest_passes,
        "emit_rewards_event",
        AsyncMock(return_value={"rewards_granted": 10}),
    )
    yield SimpleNamespace(factory=factory, sender=sender, provider=provider)
    await engine.dispose()


async def make_session(env, **overrides):
    now = utc_now()
    values = dict(
        session_type="community",
        status="scheduled",
        title="Test guest swim",
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=4),
        capacity=2,
        pool_fee=100000,
        guest_fee_kobo=500000,
        allows_guests=True,
        guest_booking_mode="public",
        guest_reconciliation_days=3,
        location_name="Private test venue",
        location_address="Exact test address",
    )
    async with env.factory() as db:
        values.update(overrides)
        values["session_type"] = SessionType(values["session_type"])
        values["status"] = SessionStatus(values["status"])
        session = Session(**values)
        db.add(session)
        await db.commit()
        return session


def guest_input(**overrides):
    values = dict(
        full_name="Ada Guest",
        email="guest@example.com",
        phone="080" + str(uuid.uuid4().int)[:8],
        waiver_accepted=True,
        marketing_consent=False,
    )
    return GuestPassCreate(**(values | overrides))


async def create(env, session, **overrides):
    async with env.factory() as db:
        return await guest_passes.create_guest_pass(
            session.id, guest_input(**overrides), db=db
        )


async def confirm(env, result):
    async with env.factory() as db:
        return await guest_passes.confirm_guest_pass(
            result["id"],
            GuestPassConfirm(payment_reference=result["payment_reference"]),
            _service=SimpleNamespace(user_id="payments"),
            db=db,
        )


@pytest.mark.asyncio
async def test_free_guest_is_confirmed_without_provider_or_attendance(guest_env):
    session = await make_session(guest_env, guest_fee_kobo=0)
    result = await create(guest_env, session)
    assert result["status"] == "confirmed"
    assert result["total_kobo"] == 0
    assert result["attendance_recorded"] is False
    assert result["receipt_url"].endswith(guest_booking.receipt_token(result["id"]))
    guest_env.provider.assert_not_awaited()
    guest_env.sender.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["paystack", "manual_transfer"])
async def test_recent_settlement_has_no_hold_capacity_or_automatic_attendance(
    guest_env, method
):
    now = utc_now()
    session = await make_session(
        guest_env,
        starts_at=now - timedelta(days=1),
        ends_at=now - timedelta(hours=22),
        status="completed",
        capacity=0,
    )
    result = await create(
        guest_env,
        session,
        payment_method=method,
        booking_source="instagram",
        campaign_key="september_swim",
    )
    assert result["booking_mode"] == "settlement"
    assert result["reservation_expires_at"] is None
    payload = guest_env.provider.await_args.kwargs["json"]
    assert payload["payment_method"] == method
    assert payload["metadata"]["reservation_expires_at"] is None
    confirmed = await confirm(guest_env, result)
    assert confirmed.status == "confirmed"
    assert confirmed.attended_at is None
    assert confirmed.referrer_auth_id is None
    assert confirmed.booking_source == "instagram"
    assert confirmed.campaign_key == "september_swim"
    assert guest_env.sender.await_args.kwargs["template_data"]["post_session"] is True


@pytest.mark.asyncio
async def test_concurrent_guest_checkouts_cannot_oversell(guest_env):
    session = await make_session(guest_env, capacity=1)
    results = await asyncio.gather(
        create(guest_env, session), create(guest_env, session), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(getattr(r, "status_code", None) == 409 for r in results) == 1


@pytest.mark.asyncio
async def test_member_reservation_counts_standalone_guest_hold(guest_env):
    session = await make_session(guest_env, capacity=1)
    await create(guest_env, session)
    async with guest_env.factory() as db:
        with pytest.raises(Exception) as error:
            await assert_booking_capacity(
                db, session=session, member_id=uuid.uuid4(), new_party_size=1
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_member_and_guest_concurrent_capacity_boundary(guest_env):
    session = await make_session(guest_env, capacity=1)

    async def member():
        async with guest_env.factory() as db:
            member_id = uuid.uuid4()
            await assert_booking_capacity(
                db, session=session, member_id=member_id, new_party_size=1
            )
            booking = SessionBooking(
                session_id=session.id,
                member_id=member_id,
                member_auth_id="test-member",
                status="confirmed",
            )
            db.add(booking)
            await db.commit()
            return {"id": booking.id}

    results = await asyncio.gather(
        member(), create(guest_env, session), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(getattr(r, "status_code", None) == 409 for r in results) == 1


@pytest.mark.asyncio
async def test_repeated_and_concurrent_callbacks_send_one_confirmation(guest_env):
    session = await make_session(guest_env)
    result = await create(guest_env, session, referral_code="ADA")
    await asyncio.gather(confirm(guest_env, result), confirm(guest_env, result))
    await confirm(guest_env, result)
    guest_env.sender.assert_awaited_once()
    async with guest_env.factory() as db:
        guest = await db.get(GuestPass, result["id"])
        assert guest.confirmation_email_sent_at
        assert guest.attended_at is None
        assert guest.referral_reward_status != "granted"


@pytest.mark.asyncio
async def test_failed_email_remains_retryable_without_losing_confirmation(guest_env):
    guest_env.sender.side_effect = [False, True]
    session = await make_session(guest_env)
    result = await create(guest_env, session)
    assert (await confirm(guest_env, result)).status == "confirmed"
    async with guest_env.factory() as db:
        guest = await db.get(GuestPass, result["id"])
        assert guest.confirmation_email_sent_at is None
        row = (
            await db.execute(
                select(BookingEmailDelivery).where(
                    BookingEmailDelivery.booking_id == guest.id
                )
            )
        ).scalar_one()
        assert row.sent_at is None and row.attempts == 1
    await confirm(guest_env, result)
    await confirm(guest_env, result)
    assert guest_env.sender.await_count == 2


@pytest.mark.asyncio
async def test_provider_failure_returns_private_receipt_and_can_resume(guest_env):
    guest_env.provider.side_effect = [
        httpx.ConnectError("offline"),
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://payments.test"),
            json={
                "authorization_url": "https://checkout.test/retry",
                "amount_kobo": 500000,
            },
        ),
    ]
    session = await make_session(guest_env)
    result = await create(guest_env, session)
    assert result["status"] == "payment_failed" and result["receipt_url"]
    token = guest_booking.receipt_token(result["id"])
    async with guest_env.factory() as db:
        with pytest.raises(Exception) as error:
            await guest_passes.retry_guest_checkout(
                result["id"], db=db, x_guest_pass_token="invalid"
            )
        assert error.value.status_code == 404
        retry = await guest_passes.retry_guest_checkout(
            result["id"], db=db, x_guest_pass_token=token
        )
    assert retry["checkout_url"] == "https://checkout.test/retry"
    refs = [
        call.kwargs["json"]["reference"] for call in guest_env.provider.await_args_list
    ]
    assert len(set(refs)) == 1


@pytest.mark.asyncio
async def test_old_session_needs_email_bound_single_use_admin_link(guest_env):
    now = utc_now()
    session = await make_session(
        guest_env,
        starts_at=now - timedelta(days=30),
        ends_at=now - timedelta(days=29),
        status="completed",
    )
    with pytest.raises(Exception) as error:
        await create(guest_env, session)
    assert error.value.status_code == 422
    async with guest_env.factory() as db:
        issued = await guest_booking_admin.issue_guest_link(
            session.id,
            GuestBookingGrantCreate(email="approved@example.com"),
            admin=SimpleNamespace(user_id="admin"),
            db=db,
        )
    token = parse_qs(urlparse(issued.url).fragment)["token"][0]
    with pytest.raises(Exception) as error:
        await create(guest_env, session, access_token=token)
    assert error.value.status_code == 403
    result = await create(
        guest_env, session, access_token=token, email="approved@example.com"
    )
    assert result["booking_mode"] == "settlement"
    with pytest.raises(Exception) as error:
        await create(
            guest_env, session, access_token=token, email="approved@example.com"
        )
    assert error.value.status_code == 403
    async with guest_env.factory() as db:
        grant = await db.get(GuestBookingGrant, issued.id)
        assert grant.token_hash != token and grant.used_by_pass_id == result["id"]


@pytest.mark.asyncio
async def test_private_offer_and_receipt_do_not_leak_venue_without_capability(
    guest_env,
):
    session = await make_session(guest_env, guest_location_private=True)
    async with guest_env.factory() as db:
        offer = await guest_passes.guest_pass_offer(
            session.id, db=db, x_guest_booking_token=None
        )
        assert offer.location_name is None
    result = await create(guest_env, session)
    await confirm(guest_env, result)
    async with guest_env.factory() as db:
        public = await guest_passes.get_guest_pass_status(
            result["id"], db=db, x_guest_pass_token=None
        )
        private = await guest_passes.get_guest_pass_status(
            result["id"],
            db=db,
            x_guest_pass_token=guest_booking.receipt_token(result["id"]),
        )
        assert public["location_name"] is None and public["location_address"] is None
        assert private["location_name"] == "Private test venue"
        assert "email" not in private and "phone" not in private


@pytest.mark.asyncio
async def test_expired_reservation_payment_cannot_take_last_member_seat(guest_env):
    session = await make_session(guest_env, capacity=1)
    result = await create(guest_env, session)
    async with guest_env.factory() as db:
        guest = await db.get(GuestPass, result["id"])
        guest.reservation_expires_at = utc_now() - timedelta(minutes=1)
        db.add(
            SessionBooking(
                session_id=session.id,
                member_id=uuid.uuid4(),
                member_auth_id="member",
                status="confirmed",
            )
        )
        await db.commit()
    with pytest.raises(Exception) as error:
        await confirm(guest_env, result)
    assert error.value.status_code == 409
    guest_env.sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_attendance_is_required_before_referral_reward(guest_env):
    session = await make_session(guest_env)
    result = await create(guest_env, session, referral_code="ADA")
    await confirm(guest_env, result)
    guest_passes.emit_rewards_event.assert_not_awaited()
    async with guest_env.factory() as db:
        guest = await guest_passes.mark_guest_pass_attended(
            result["id"],
            GuestPassAttendanceUpdate(
                actual_swim_minutes=90, send_assessment_email=False
            ),
            _admin=SimpleNamespace(user_id="admin"),
            db=db,
        )
        assert guest.attended_at and guest.actual_swim_minutes == 90
        assert guest.referral_reward_status == "granted"
    guest_passes.emit_rewards_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_member_confirmation_uses_same_outbox_for_paid_and_walkin(
    guest_env,
):
    session = await make_session(guest_env)
    for original_status in ["pending", "confirmed"]:
        async with guest_env.factory() as db:
            booking = SessionBooking(
                session_id=session.id,
                member_id=uuid.uuid4(),
                member_auth_id="member",
                status=original_status,
                fee_amount_kobo=350000,
                expires_at=utc_now() + timedelta(minutes=10),
            )
            db.add(booking)
            await db.commit()
            request = BookingConfirmRequest(
                payment_intent_id=uuid.uuid4(), member_auth_id="member"
            )
            await internal.internal_confirm_booking(
                booking.id, request, _=SimpleNamespace(user_id="payments"), db=db
            )
            await internal.internal_confirm_booking(
                booking.id, request, _=SimpleNamespace(user_id="payments"), db=db
            )
            assert booking.confirmation_email_sent_at
            assert booking.payment_intent_id == request.payment_intent_id
    assert guest_env.sender.await_count == 2
    for call in guest_env.sender.await_args_list:
        assert call.kwargs["template_type"] == "session_confirmation"
        assert call.kwargs["template_data"]["guest_booking_url"].endswith("ref=ADA")


@pytest.mark.asyncio
async def test_email_exception_preserves_paid_booking_and_retries(guest_env):
    guest_env.sender.side_effect = [httpx.ConnectError("offline"), True]
    session = await make_session(guest_env)
    result = await create(guest_env, session)
    booking = await confirm(guest_env, result)
    assert booking.status == "confirmed" and booking.confirmation_email_sent_at is None
    assert (await confirm(guest_env, result)).confirmation_email_sent_at
    assert guest_env.sender.await_count == 2


@pytest.mark.asyncio
async def test_full_bubbles_confirmation_has_zero_cash_and_standard_branding(guest_env):
    session = await make_session(guest_env)
    async with guest_env.factory() as db:
        booking = SessionBooking(
            session_id=session.id,
            member_id=uuid.uuid4(),
            member_auth_id="member",
            status="confirmed",
            fee_amount_kobo=350000,
            wallet_transaction_id=uuid.uuid4(),
        )
        db.add(booking)
        await db.flush()
        key = await booking_confirmation.queue_confirmation(db, booking.id)
        await db.commit()
        await booking_confirmation.deliver_confirmation(db, key)
    message = guest_env.sender.await_args.kwargs
    assert message["template_type"] == "session_confirmation"
    assert message["template_data"]["bubbles_applied"] == 35
    assert message["template_data"]["bubbles_amount_ngn"] == 3500
    assert message["template_data"]["amount_paid"] == 0


@pytest.mark.asyncio
async def test_payment_snapshot_survives_email_failure(guest_env):
    guest_env.sender.side_effect = [False, True]
    session = await make_session(guest_env)
    async with guest_env.factory() as db:
        booking = SessionBooking(
            session_id=session.id,
            member_id=uuid.uuid4(),
            member_auth_id="member",
            status="pending",
            fee_amount_kobo=350000,
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.add(booking)
        await db.commit()
        request = BookingConfirmRequest(
            payment_intent_id=uuid.uuid4(),
            member_auth_id="member",
            confirmation_details={
                "amount_paid": 2700,
                "bubbles_applied": 10,
                "bubbles_amount_ngn": 1000,
                "payment_reference": "TEST-SNAPSHOT",
            },
        )
        await internal.internal_confirm_booking(
            booking.id, request, _=SimpleNamespace(user_id="payments"), db=db
        )
        await booking_confirmation.deliver_confirmation(
            db, f"member-booking-confirmation:{booking.id}"
        )
    for call in guest_env.sender.await_args_list:
        data = call.kwargs["template_data"]
        assert data["amount_paid"] == 2700 and data["bubbles_applied"] == 10
        assert data["payment_reference"] == "TEST-SNAPSHOT"


@pytest.mark.asyncio
async def test_admin_restores_expired_pass_as_settlement_without_duplicate(guest_env):
    session = await make_session(guest_env)
    result = await create(guest_env, session, payment_method="manual_transfer")
    async with guest_env.factory() as db:
        swim = await db.get(Session, session.id)
        swim.starts_at = utc_now() - timedelta(days=30)
        swim.ends_at = utc_now() - timedelta(days=29)
        guest = await db.get(GuestPass, result["id"])
        guest.reservation_expires_at = utc_now() - timedelta(days=30)
        await db.commit()
        restored = await guest_booking_admin.restore_guest_payment_link(
            guest.id, _admin=SimpleNamespace(user_id="admin"), db=db
        )
        assert restored["url"] == result["receipt_url"]
        assert (
            guest.booking_mode == "settlement" and guest.reservation_expires_at is None
        )
        assert guest.attended_at is None
        await guest_checkout.start_checkout(db, guest.id)
    payload = guest_env.provider.await_args.kwargs["json"]
    assert payload["reference"] == result["payment_reference"]
    assert payload["metadata"]["reservation_expires_at"] is None


@pytest.mark.asyncio
async def test_admin_cannot_restore_expired_guest_over_existing_member(guest_env):
    session = await make_session(guest_env, capacity=1)
    result = await create(guest_env, session)
    async with guest_env.factory() as db:
        guest = await db.get(GuestPass, result["id"])
        guest.reservation_expires_at = utc_now() - timedelta(minutes=1)
        db.add(
            SessionBooking(
                session_id=session.id,
                member_id=uuid.uuid4(),
                member_auth_id="member",
                status="confirmed",
            )
        )
        await db.commit()
        with pytest.raises(Exception) as error:
            await guest_booking_admin.restore_guest_payment_link(
                guest.id, _admin=SimpleNamespace(user_id="admin"), db=db
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_roster_and_funnel_keep_booking_and_attendance_distinct(
    guest_env, monkeypatch
):
    from services.sessions_service.models import BookingGuest
    from services.sessions_service.routers import session_roster
    from services.sessions_service.schemas.guest_pass import GuestLinkEventCreate

    session = await make_session(guest_env, capacity=10)
    guest = await create(guest_env, session, booking_source="instagram")
    await confirm(guest_env, guest)
    member_id = uuid.uuid4()
    monkeypatch.setattr(
        session_roster,
        "get_members_bulk",
        AsyncMock(
            return_value=[
                {"id": str(member_id), "first_name": "Ada", "last_name": "Member"}
            ]
        ),
    )
    monkeypatch.setattr(
        session_roster,
        "internal_get",
        AsyncMock(
            return_value=httpx.Response(
                200, json=[], request=httpx.Request("GET", "https://attendance.test")
            )
        ),
    )
    async with guest_env.factory() as db:
        member = SessionBooking(
            session_id=session.id,
            member_id=member_id,
            member_auth_id="member",
            status="confirmed",
            party_size=2,
        )
        db.add(member)
        await db.flush()
        db.add(BookingGuest(booking_id=member.id, full_name="Attached Guest"))
        await db.commit()
        event = GuestLinkEventCreate(
            id=uuid.uuid4(), event_type="view", booking_source="instagram"
        )
        await guest_booking_admin.record_guest_link_event(session.id, event, db=db)
        await guest_booking_admin.record_guest_link_event(session.id, event, db=db)
        roster = await session_roster.session_roster(
            session.id, _admin=SimpleNamespace(user_id="admin"), db=db
        )
        assert {r.kind for r in roster.entries} == {
            "member",
            "booking_guest",
            "guest_pass",
        }
        assert all(r.attendance_status is None for r in roster.entries)
        funnel = await guest_booking_admin.guest_funnel(
            session_id=session.id,
            booking_source="instagram",
            campaign_key=None,
            _admin=SimpleNamespace(user_id="admin"),
            db=db,
        )
        assert (
            funnel.link_views == 1 and funnel.checkout_started == 1 and funnel.paid == 1
        )
        assert funnel.attended == 0 and funnel.assessed == 0


@pytest.mark.asyncio
async def test_member_cannot_forge_a_payment_confirmation(guest_env):
    from services.sessions_service.routers import bookings

    session = await make_session(guest_env)
    async with guest_env.factory() as db:
        row = SessionBooking(
            session_id=session.id,
            member_id=uuid.uuid4(),
            member_auth_id="member",
            status="pending",
            fee_amount_kobo=500000,
        )
        db.add(row)
        await db.commit()
        with pytest.raises(Exception) as error:
            await bookings.confirm_booking(
                row.id,
                BookingConfirmRequest(payment_intent_id=uuid.uuid4()),
                current_user=SimpleNamespace(user_id="member"),
                db=db,
            )
        assert error.value.status_code == 409
        assert row.status == "pending" and row.payment_intent_id is None
    guest_env.sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_or_expired_approval_cannot_create_a_pass(guest_env):
    session = await make_session(guest_env, guest_booking_mode="approval_required")
    for expired in [False, True]:
        async with guest_env.factory() as db:
            issued = await guest_booking_admin.issue_guest_link(
                session.id,
                GuestBookingGrantCreate(email="guest@example.com"),
                admin=SimpleNamespace(user_id="admin"),
                db=db,
            )
            if expired:
                grant = await db.get(GuestBookingGrant, issued.id)
                grant.expires_at = utc_now() - timedelta(seconds=1)
                await db.commit()
            else:
                await guest_booking_admin.revoke_guest_link(
                    issued.id, _admin=SimpleNamespace(user_id="admin"), db=db
                )
        token = parse_qs(urlparse(issued.url).fragment)["token"][0]
        with pytest.raises(Exception) as error:
            await create(guest_env, session, access_token=token)
        assert error.value.status_code == 403
    guest_env.provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_guest_endpoints_and_admin_authentication(guest_env):
    from libs.db.session import get_async_db
    from services.sessions_service.app.main import create_app

    app = create_app()

    async def local_db():
        async with guest_env.factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = local_db
    session = await make_session(guest_env, guest_fee_kobo=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sessions.test"
    ) as client:
        offer = await client.get(f"/sessions/{session.id}/guest-pass")
        assert (
            offer.status_code == 200 and offer.json()["booking_mode"] == "reservation"
        )
        created = await client.post(
            f"/sessions/{session.id}/guest-passes",
            json=guest_input().model_dump(mode="json"),
        )
        assert created.status_code == 201, created.text
        body = created.json()
        public = await client.get(f"/guest-passes/{body['id']}")
        assert public.status_code == 200 and "email" not in public.json()
        unauthorized = await client.post(f"/guest-passes/{body['id']}/checkout")
        assert unauthorized.status_code == 404
        retry = await client.post(
            f"/guest-passes/{body['id']}/checkout",
            headers={
                "X-Guest-Pass-Token": guest_booking.receipt_token(uuid.UUID(body["id"]))
            },
        )
        assert retry.status_code == 200 and retry.json()["status"] == "confirmed"
        for path in [
            "/admin/guest-passes",
            "/admin/guest-passes/funnel",
            f"/admin/sessions/{session.id}/roster",
            "/admin/sessions/guest-booking-options",
        ]:
            response = await client.get(path)
            assert response.status_code in {401, 403}, path
        response = await client.post(f"/admin/guest-passes/{body['id']}/payment-link")
        assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_reusable_referral_code_does_not_authorize_member_invite(guest_env):
    session = await make_session(guest_env, guest_booking_mode="member_invite")
    with pytest.raises(Exception) as error:
        await create(guest_env, session, referral_code="UGO123")
    assert error.value.status_code == 403
    guest_env.provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_invite_requires_confirmed_session_booking_and_is_session_bound(
    guest_env, monkeypatch
):
    from services.sessions_service.routers import member

    session = await make_session(
        guest_env, guest_booking_mode="member_invite", capacity=10
    )
    other_session = await make_session(guest_env, guest_booking_mode="member_invite")
    wallet = AsyncMock(
        return_value=httpx.Response(
            200,
            request=httpx.Request("GET", "https://wallet.test"),
            json={"share_link": "https://swimbuddz.test/join?ref=UGO123"},
        )
    )
    monkeypatch.setattr(guest_booking, "internal_get", wallet)
    monkeypatch.setattr(
        member, "_decorate_session_for_user", AsyncMock(return_value=session)
    )
    inviter = SimpleNamespace(user_id="inviter")
    async with guest_env.factory() as db:
        assert (
            await guest_booking_admin.guest_share_link(session.id, user=inviter, db=db)
        )["url"] is None
        booking = SessionBooking(
            session_id=session.id,
            member_id=uuid.uuid4(),
            member_auth_id="inviter",
            status="pending",
        )
        db.add(booking)
        await db.commit()
        assert await guest_booking.member_guest_url(session, "inviter", db) is None
        wallet.assert_not_awaited()
        booking.status = "confirmed"
        await db.commit()
        shared = await guest_booking_admin.guest_share_link(
            session.id, user=inviter, db=db
        )
        url = shared["url"]
        token = parse_qs(urlparse(url).fragment)["invite"][0]
        assert parse_qs(urlparse(url).query)["ref"] == ["UGO123"]
        offer = await guest_passes.guest_pass_offer(
            session.id, db=db, x_guest_booking_token=None, x_guest_invite_token=token
        )
        assert offer.member_invitation_valid is True
        assert offer.approval_granted is False
        booking_id = booking.id
    with pytest.raises(Exception) as error:
        await create(
            guest_env, other_session, invite_token=token, referral_code="UGO123"
        )
    assert error.value.status_code == 403
    valid = await create(guest_env, session, invite_token=token, referral_code="UGO123")
    assert valid["status"] == "pending_payment"
    async with guest_env.factory() as db:
        booking = await db.get(SessionBooking, booking_id)
        booking.status = "cancelled"
        await db.commit()
    with pytest.raises(Exception) as error:
        await create(guest_env, session, invite_token=token, referral_code="UGO123")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_public_swim_can_be_shared_without_a_confirmed_booking(
    guest_env, monkeypatch
):
    session = await make_session(guest_env)
    monkeypatch.setattr(
        guest_booking,
        "internal_get",
        AsyncMock(
            return_value=httpx.Response(
                200,
                request=httpx.Request("GET", "https://wallet.test"),
                json={"share_link": "https://swimbuddz.test/join?ref=ADA"},
            )
        ),
    )
    async with guest_env.factory() as db:
        url = await guest_booking.member_guest_url(session, "unbooked-member", db)
        assert url and "ref=ADA" in url and "#invite=" not in url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("Wallet offline"),
        httpx.HTTPStatusError(
            "Events unavailable",
            request=httpx.Request("GET", "https://events.test"),
            response=httpx.Response(503),
        ),
    ],
)
async def test_optional_invitation_outage_never_suppresses_confirmation(
    guest_env, monkeypatch, failure
):
    monkeypatch.setattr(
        booking_confirmation, "member_guest_url", AsyncMock(side_effect=failure)
    )
    session = await make_session(guest_env)
    async with guest_env.factory() as db:
        booking = SessionBooking(
            session_id=session.id,
            member_id=uuid.uuid4(),
            member_auth_id="member",
            status="pending",
            fee_amount_kobo=350000,
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.add(booking)
        await db.commit()
        request = BookingConfirmRequest(
            payment_intent_id=uuid.uuid4(), member_auth_id="member"
        )
        await internal.internal_confirm_booking(
            booking.id, request, _=SimpleNamespace(user_id="payments"), db=db
        )
        assert booking.confirmation_email_sent_at
        await internal.internal_confirm_booking(
            booking.id, request, _=SimpleNamespace(user_id="payments"), db=db
        )
    guest_env.sender.assert_awaited_once()
    message = guest_env.sender.await_args.kwargs
    assert message["template_type"] == "session_confirmation"
    assert message["template_data"]["guest_booking_url"] is None


@pytest.mark.asyncio
async def test_roster_excludes_failed_expired_and_unpaid_settlement_attempts(
    guest_env, monkeypatch
):
    from services.sessions_service.routers import session_roster

    session = await make_session(guest_env, capacity=10)
    cases = [
        ("confirmed", "reservation", None, True),
        ("attended", "settlement", None, True),
        ("pending_payment", "reservation", utc_now() + timedelta(minutes=15), True),
        ("pending_payment", "reservation", utc_now() - timedelta(minutes=1), False),
        ("payment_failed", "reservation", utc_now() + timedelta(minutes=15), False),
        ("pending_payment", "settlement", None, False),
    ]
    included = set()
    monkeypatch.setattr(session_roster, "get_members_bulk", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        session_roster,
        "internal_get",
        AsyncMock(
            return_value=httpx.Response(
                200, json=[], request=httpx.Request("GET", "https://attendance.test")
            )
        ),
    )
    async with guest_env.factory() as db:
        for status, mode, expires, expected in cases:
            guest = GuestPass(
                session_id=session.id,
                full_name="Test Guest",
                email="guest@example.com",
                phone=str(uuid.uuid4())[:20],
                price_kobo=500000,
                total_kobo=500000,
                payment_reference=str(uuid.uuid4()),
                status=status,
                booking_mode=mode,
                reservation_expires_at=expires,
            )
            db.add(guest)
            await db.flush()
            if expected:
                included.add(guest.id)
        await db.commit()
        roster = await session_roster.session_roster(
            session.id, _admin=SimpleNamespace(user_id="admin"), db=db
        )
        assert {r.id for r in roster.entries} == included
        all_passes = await guest_passes.list_guest_passes(
            session_id=session.id, _admin=SimpleNamespace(user_id="admin"), db=db
        )
        assert len(all_passes) == len(cases)
