"""Published Pool Access inventory and unpaid-entry integration invariants."""

from datetime import timedelta
from uuid import UUID, uuid4
import pytest
import httpx
from libs.common.datetime_utils import utc_now
from services.pools_service.models.access import (
    PoolAccessBooking,
    PoolAccessOffer,
    PoolAccessAdmission,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _active_pool(client):
    identity = uuid4().hex[:10]
    created = await client.post(
        "/admin/pools",
        json={
            "name": "Test Pool " + identity,
            "slug": "test-pool-" + identity,
            "location_area": "Yaba",
            "has_lifeguard": True,
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    status = await client.post(
        f"/admin/pools/{pid}/status?partnership_status=active_partner"
    )
    assert status.status_code == 200, status.text
    return pid


async def _published_offer(client, capacity=2):
    pid = await _active_pool(client)
    start = utc_now() + timedelta(days=2)
    end = start + timedelta(hours=2)
    draft = await client.post(
        "/admin/pools/access/offers",
        json={
            "pool_id": pid,
            "title": "Self-directed practice",
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "capacity": capacity,
            "selling_price_kobo": 700000,
            "negotiated_cost_kobo": 500000,
            "cost_basis": "per_person",
            "public_booking_enabled": True,
            "self_directed_permitted": True,
            "access_rules": "Lifeguard supervision required",
            "cancellation_policy": "Cancel at least 24 hours before",
        },
    )
    assert draft.status_code == 201, draft.text
    oid = draft.json()["id"]
    result = await client.post(f"/admin/pools/access/offers/{oid}/publish")
    assert result.status_code == 200, result.text
    return oid


async def test_public_offer_hides_partner_cost(pools_client):
    oid = await _published_offer(pools_client)
    visible = await pools_client.get("/pools/access/offers")
    assert visible.status_code == 200, visible.text
    row = next(item for item in visible.json() if item["id"] == oid)
    assert row["selling_price_kobo"] == 700000
    assert "negotiated_cost_kobo" not in row
    assert "cost_basis" not in row
    assert row["pool_name"].startswith("Test Pool")
    assert row["has_lifeguard"] is True


async def test_capacity_reservation_is_idempotent_and_no_free_ticket(pools_client):
    oid = await _published_offer(pools_client, capacity=2)
    body = {
        "offer_id": oid,
        "idempotency_key": str(uuid4()),
        "accept_access_rules": True,
        "accept_cancellation_policy": True,
        "acknowledge_uncoached_activity": True,
        "guests": [{"name": "Ada Person"}, {"name": "Ben Person"}],
    }
    r = await pools_client.post("/pools/access/bookings", json=body)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "pending_payment"
    assert b["selling_total_kobo"] == 1400000
    again = await pools_client.post("/pools/access/bookings", json=body)
    assert again.status_code == 201 and again.json()["id"] == b["id"]
    over = await pools_client.post(
        "/pools/access/bookings", json={**body, "idempotency_key": str(uuid4())}
    )
    assert over.status_code == 409
    tickets = await pools_client.get(f'/pools/access/bookings/{b["id"]}/tickets')
    assert tickets.status_code == 409


async def test_unpaid_booking_confirmation_requires_verified_payment(
    pools_client, monkeypatch
):
    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("payments service unavailable")

    oid = await _published_offer(pools_client)
    booked = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": oid,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert booked.status_code == 201, booked.text
    bid = booked.json()["id"]
    my = await pools_client.get("/pools/access/bookings/me")
    assert my.status_code == 200 and any(x["id"] == bid for x in my.json())
    monkeypatch.setattr(httpx.AsyncClient, "get", unavailable)
    direct = await pools_client.post(
        f"/internal/pools/access/bookings/{bid}/confirm",
        json={
            "member_auth_id": "spoofed",
            "payment_reference": "PAY-fake",
            "amount_kobo": 700000,
        },
    )
    assert direct.status_code == 503
    assert "temporarily unavailable" in direct.json()["detail"]


async def test_paid_evidence_activates_exact_booking_only(
    pools_client, db_session, monkeypatch
):
    oid = await _published_offer(pools_client)
    booked = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": oid,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert booked.status_code == 201, booked.text
    bid = booked.json()["id"]
    buyer = await db_session.get(PoolAccessBooking, UUID(bid))
    buyer_auth_id = buyer.buyer_auth_id
    ref = "PAY-" + uuid4().hex
    claim = await pools_client.post(
        f"/internal/pools/access/bookings/{bid}/claim-checkout",
        json={"member_auth_id": buyer_auth_id, "payment_reference": ref},
    )
    assert claim.status_code == 200, claim.text

    async def paid_evidence(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "reference": ref,
                "booking_id": bid,
                "member_auth_id": buyer_auth_id,
                "amount_kobo": 700000,
                "currency": "NGN",
            },
        )

    with monkeypatch.context() as patch:
        patch.setattr(httpx.AsyncClient, "get", paid_evidence)
        confirm = await pools_client.post(
            f"/internal/pools/access/bookings/{bid}/confirm",
            json={
                "member_auth_id": buyer_auth_id,
                "payment_reference": ref,
                "amount_kobo": 700000,
            },
        )
    assert confirm.status_code == 200, confirm.text

    monkeypatch.setenv("POOL_ACCESS_QR_SECRET", "secure-fixture-" * 4)
    tickets = await pools_client.get(f"/pools/access/bookings/{bid}/tickets")
    assert tickets.status_code == 200, tickets.text
    assert len(tickets.json()) == 1
    assert tickets.json()[0]["ticket"].startswith("pa1.")


async def test_unclaimed_hold_can_cancel_and_release_capacity(pools_client):
    oid = await _published_offer(pools_client, capacity=1)
    first = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": oid,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert first.status_code == 201, first.text
    booking_id = first.json()["id"]
    cancelled = await pools_client.post(f"/pools/access/bookings/{booking_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    replacement = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": oid,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ben Person"}],
        },
    )
    assert replacement.status_code == 201, replacement.text


async def test_checkout_claim_prevents_unverified_cancellation(
    pools_client, db_session
):
    oid = await _published_offer(pools_client, capacity=1)
    reservation = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": oid,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert reservation.status_code == 201, reservation.text
    booking_id = reservation.json()["id"]
    buyer = await db_session.get(PoolAccessBooking, UUID(booking_id))
    claim = await pools_client.post(
        f"/internal/pools/access/bookings/{booking_id}/claim-checkout",
        json={
            "member_auth_id": buyer.buyer_auth_id,
            "payment_reference": "PAY-" + uuid4().hex,
        },
    )
    assert claim.status_code == 200, claim.text
    cancellation = await pools_client.post(
        f"/pools/access/bookings/{booking_id}/cancel"
    )
    assert cancellation.status_code == 409


async def test_member_cannot_request_refund_for_unpaid_visit(pools_client):
    offer_id = await _published_offer(pools_client)
    created = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": offer_id,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert created.status_code == 201
    booking_id = created.json()["id"]
    response = await pools_client.post(
        f"/pools/access/bookings/{booking_id}/request-cancellation",
        json={"reason": "My schedule has changed"},
    )
    assert response.status_code == 409


async def test_partner_settlement_is_limited_to_verified_admissions(
    pools_client, db_session, monkeypatch
):
    offer_id = await _published_offer(pools_client)
    created = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": offer_id,
            "idempotency_key": str(uuid4()),
            "accept_access_rules": True,
            "accept_cancellation_policy": True,
            "acknowledge_uncoached_activity": True,
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert created.status_code == 201
    booking_id = created.json()["id"]
    booking = await db_session.get(PoolAccessBooking, UUID(booking_id))
    reference = "PAY-" + uuid4().hex
    claim = await pools_client.post(
        f"/internal/pools/access/bookings/{booking_id}/claim-checkout",
        json={"member_auth_id": booking.buyer_auth_id, "payment_reference": reference},
    )
    assert claim.status_code == 200, claim.text

    async def provider_evidence(*args, **kwargs):
        return httpx.Response(
            200,
            json={
                "reference": reference,
                "booking_id": booking_id,
                "member_auth_id": booking.buyer_auth_id,
                "amount_kobo": 700000,
                "currency": "NGN",
            },
        )

    with monkeypatch.context() as patch:
        patch.setattr(httpx.AsyncClient, "get", provider_evidence)
        confirmed = await pools_client.post(
            f"/internal/pools/access/bookings/{booking_id}/confirm",
            json={
                "member_auth_id": booking.buyer_auth_id,
                "payment_reference": reference,
                "amount_kobo": 700000,
            },
        )
    assert confirmed.status_code == 200, confirmed.text
    offer = await db_session.get(PoolAccessOffer, UUID(offer_id))
    offer.starts_at = utc_now() - timedelta(hours=3)
    offer.ends_at = utc_now() - timedelta(hours=1)
    from sqlalchemy import select

    admission = (
        await db_session.execute(
            select(PoolAccessAdmission).where(
                PoolAccessAdmission.booking_id == UUID(booking_id)
            )
        )
    ).scalar_one()
    admission.checked_in_at = utc_now() - timedelta(hours=2)
    await db_session.commit()

    recon = await pools_client.post(f"/pools/access/bookings/{booking_id}/reconcile")
    assert recon.status_code == 200, recon.text
    admin_records = await pools_client.get("/admin/pools/access/bookings")
    reconciliation_id = next(
        row["reconciliation_id"]
        for row in admin_records.json()
        if row["booking_id"] == booking_id
    )
    url = (
        f"/pools/access/reconciliations/{reconciliation_id}/record-external-settlement"
    )
    first = await pools_client.post(
        url,
        json={
            "amount_kobo": 200000,
            "bank_reference": "BANK-FIRST-" + uuid4().hex,
            "evidence_note": "Confirmed transfer from operations account",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["remaining_kobo"] == 300000
    over = await pools_client.post(
        url,
        json={
            "amount_kobo": 300001,
            "bank_reference": "BANK-OVER-" + uuid4().hex,
            "evidence_note": "Invalid excess bank transaction",
        },
    )
    assert over.status_code == 409, over.text


async def test_booking_requires_explicit_facility_and_uncoached_consent(pools_client):
    offer_id = await _published_offer(pools_client)
    result = await pools_client.post(
        "/pools/access/bookings",
        json={
            "offer_id": offer_id,
            "idempotency_key": str(uuid4()),
            "guests": [{"name": "Ada Person"}],
        },
    )
    assert result.status_code == 422
