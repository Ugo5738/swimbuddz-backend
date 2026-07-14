import uuid

import httpx
import pytest

from services.payments_service.routers.intents import intent_creation


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_session_access_contract_requires_consistent_booking_fields(monkeypatch):
    session_id = uuid.uuid4()

    async def fake_internal_get(**kwargs):
        assert kwargs["params"] == {"member_auth_id": "auth-1"}
        return FakeResponse(
            200,
            {
                "member_id": str(uuid.uuid4()),
                "confirmed_booking": True,
                "confirmed_booking_id": None,
                "required_tier": "community",
                "visible": True,
                "bookable": False,
                "digest_eligible": True,
                "prompt_eligible": False,
                "sign_in_allowed": True,
            },
        )

    monkeypatch.setattr(intent_creation, "internal_get", fake_internal_get)

    with pytest.raises(Exception) as exc:
        await intent_creation._get_internal_session_access(
            session_id=session_id,
            member_auth_id="auth-1",
        )

    assert getattr(exc.value, "status_code", None) == 502


@pytest.mark.asyncio
async def test_bundle_quote_combines_server_session_and_ride_totals(monkeypatch):
    payment_id = uuid.uuid4()
    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()
    member_id = uuid.uuid4()
    ride_config_id = uuid.uuid4()
    pickup_id = uuid.uuid4()

    async def fake_internal_post(**kwargs):
        if kwargs["path"].endswith("/reserve"):
            assert kwargs["json"]["payment_intent_id"] == str(payment_id)
            return FakeResponse(
                200,
                {
                    "member_id": str(member_id),
                    "payment_intent_id": str(payment_id),
                    "pool_total_kobo": 350000,
                    "lines": [
                        {
                            "session_id": str(session_id),
                            "booking_id": str(booking_id),
                            "amount_kobo": 350000,
                        }
                    ],
                },
            )
        assert kwargs["path"] == "/internal/transport/ride-quotes"
        return FakeResponse(
            200,
            {
                "total_kobo": 150000,
                "lines": [
                    {
                        "session_id": str(session_id),
                        "ride_config_id": str(ride_config_id),
                        "pickup_location_id": str(pickup_id),
                        "num_seats": 1,
                        "unit_amount_kobo": 150000,
                        "amount_kobo": 150000,
                    }
                ],
            },
        )

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    result = await intent_creation._reserve_and_quote_session_bundle(
        member_auth_id="auth-1",
        payment_intent_id=payment_id,
        session_ids=[session_id],
        ride_configs={
            str(session_id): {
                "ride_config_id": str(ride_config_id),
                "pickup_location_id": str(pickup_id),
                "num_seats": 1,
            }
        },
    )

    assert result["total_kobo"] == 500000
    assert result["reservation"]["lines"][0]["booking_id"] == str(booking_id)


@pytest.mark.asyncio
async def test_bundle_quote_releases_sessions_when_transport_rejects(monkeypatch):
    payment_id = uuid.uuid4()
    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()
    released = False

    async def fake_internal_post(**kwargs):
        nonlocal released
        if kwargs["path"].endswith("/reserve"):
            return FakeResponse(
                200,
                {
                    "member_id": str(uuid.uuid4()),
                    "payment_intent_id": str(payment_id),
                    "pool_total_kobo": 350000,
                    "lines": [
                        {
                            "session_id": str(session_id),
                            "booking_id": str(booking_id),
                            "amount_kobo": 350000,
                        }
                    ],
                },
            )
        if kwargs["path"].endswith("/release"):
            released = True
            return FakeResponse(200, {"released": 1})
        return FakeResponse(400, {"detail": "Ride config is invalid"})

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    with pytest.raises(Exception) as exc:
        await intent_creation._reserve_and_quote_session_bundle(
            member_auth_id="auth-1",
            payment_intent_id=payment_id,
            session_ids=[session_id],
            ride_configs={
                str(session_id): {
                    "ride_config_id": str(uuid.uuid4()),
                    "pickup_location_id": str(uuid.uuid4()),
                    "num_seats": 1,
                }
            },
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert released is True


@pytest.mark.asyncio
async def test_bundle_quote_releases_invalid_session_quote(monkeypatch):
    payment_id = uuid.uuid4()
    session_id = uuid.uuid4()
    released = False

    async def fake_internal_post(**kwargs):
        nonlocal released
        if kwargs["path"].endswith("/reserve"):
            return FakeResponse(
                200,
                {
                    "member_id": str(uuid.uuid4()),
                    "payment_intent_id": str(payment_id),
                    "pool_total_kobo": 350000,
                    "lines": [
                        {
                            "session_id": str(session_id),
                            "booking_id": str(uuid.uuid4()),
                            "amount_kobo": 1,
                        }
                    ],
                },
            )
        if kwargs["path"].endswith("/release"):
            released = True
            return FakeResponse(200, {"released": 1})
        raise AssertionError(f"Unexpected call: {kwargs['path']}")

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    with pytest.raises(Exception) as exc:
        await intent_creation._reserve_and_quote_session_bundle(
            member_auth_id="auth-1",
            payment_intent_id=payment_id,
            session_ids=[session_id],
            ride_configs={},
        )

    assert getattr(exc.value, "status_code", None) == 502
    assert released is True


@pytest.mark.asyncio
async def test_bundle_quote_releases_invalid_ride_quote(monkeypatch):
    payment_id = uuid.uuid4()
    session_id = uuid.uuid4()
    ride_config_id = uuid.uuid4()
    pickup_id = uuid.uuid4()
    released = False

    async def fake_internal_post(**kwargs):
        nonlocal released
        if kwargs["path"].endswith("/reserve"):
            return FakeResponse(
                200,
                {
                    "member_id": str(uuid.uuid4()),
                    "payment_intent_id": str(payment_id),
                    "pool_total_kobo": 350000,
                    "lines": [
                        {
                            "session_id": str(session_id),
                            "booking_id": str(uuid.uuid4()),
                            "amount_kobo": 350000,
                        }
                    ],
                },
            )
        if kwargs["path"].endswith("/release"):
            released = True
            return FakeResponse(200, {"released": 1})
        return FakeResponse(
            200,
            {
                "total_kobo": 150000,
                "lines": [
                    {
                        "session_id": str(session_id),
                        "ride_config_id": str(ride_config_id),
                        "pickup_location_id": str(pickup_id),
                        "num_seats": 1,
                        "unit_amount_kobo": 150000,
                        "amount_kobo": 1,
                    }
                ],
            },
        )

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    with pytest.raises(Exception) as exc:
        await intent_creation._reserve_and_quote_session_bundle(
            member_auth_id="auth-1",
            payment_intent_id=payment_id,
            session_ids=[session_id],
            ride_configs={
                str(session_id): {
                    "ride_config_id": str(ride_config_id),
                    "pickup_location_id": str(pickup_id),
                    "num_seats": 1,
                }
            },
        )

    assert getattr(exc.value, "status_code", None) == 502
    assert released is True


@pytest.mark.asyncio
async def test_bundle_reservation_network_failure_is_fail_closed(monkeypatch):
    async def fake_internal_post(**kwargs):
        request = httpx.Request("POST", "http://sessions/reserve")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    with pytest.raises(Exception) as exc:
        await intent_creation._reserve_and_quote_session_bundle(
            member_auth_id="auth-1",
            payment_intent_id=uuid.uuid4(),
            session_ids=[uuid.uuid4()],
            ride_configs={},
        )

    assert getattr(exc.value, "status_code", None) == 503


@pytest.mark.asyncio
async def test_session_booking_quote_rejects_cross_member_booking(monkeypatch):
    booking_id = uuid.uuid4()

    async def fake_internal_get(**kwargs):
        assert kwargs["path"].endswith(str(booking_id))
        return FakeResponse(
            200,
            {
                "id": str(booking_id),
                "session_id": str(uuid.uuid4()),
                "member_id": str(uuid.uuid4()),
                "member_auth_id": "other-auth",
                "status": "pending",
                "fee_amount_kobo": 350000,
                "payment_intent_id": None,
                "wallet_transaction_id": None,
            },
        )

    monkeypatch.setattr(intent_creation, "internal_get", fake_internal_get)

    with pytest.raises(Exception) as exc:
        await intent_creation._get_session_booking_quote(
            booking_id=booking_id,
            member_auth_id="current-auth",
        )

    assert getattr(exc.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_single_ride_quote_uses_transport_total(monkeypatch):
    session_id = uuid.uuid4()
    ride_config_id = uuid.uuid4()
    pickup_id = uuid.uuid4()

    async def fake_internal_post(**kwargs):
        return FakeResponse(
            200,
            {
                "total_kobo": 300000,
                "lines": [
                    {
                        "session_id": str(session_id),
                        "ride_config_id": str(ride_config_id),
                        "pickup_location_id": str(pickup_id),
                        "num_seats": 2,
                        "unit_amount_kobo": 150000,
                        "amount_kobo": 300000,
                    }
                ],
            },
        )

    monkeypatch.setattr(intent_creation, "internal_post", fake_internal_post)

    quote = await intent_creation._quote_ride_selection(
        member_id=str(uuid.uuid4()),
        session_id=session_id,
        ride_config_id=ride_config_id,
        pickup_location_id=pickup_id,
        num_seats=2,
    )

    assert quote["total_kobo"] == 300000
