"""Admission quote regression checks for all admin member attendance routes."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.sessions_service.routers import bookings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_type,source,price,audience",
    [
        ("club", "club_enrollment", 0, "club"),
        ("club", "club_visit", 1500000, "club"),
        ("club", "community_dropin", 1200000, "community"),
        ("community", "community_membership", 700000, "community"),
        ("event", "event_policy", 1250000, "community"),
    ],
)
async def test_admin_quote_uses_service_access_price(
    monkeypatch, session_type, source, price, audience
):
    session = SimpleNamespace(
        session_type=session_type,
        status="scheduled",
        id=uuid.uuid4(),
        cohort_id=None,
    )
    evaluated = SimpleNamespace(
        sign_in_eligible=True,
        reason=None,
        price_label="Verified session rate",
        access_source=source,
        fee_amount_kobo=price,
        pricing_audience=audience,
    )
    getter = AsyncMock(return_value=evaluated)
    monkeypatch.setattr(bookings, "evaluate_member_session_access", getter)
    result = await bookings._admin_member_admission_quote(
        session, uuid.uuid4(), SimpleNamespace()
    )
    assert result["allowed"]
    assert result["fee_amount_kobo"] == price
    assert result["access_source"] == source
    assert result["pricing_audience"] == audience
    getter.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["membership_required", "club_required", "community_dropins_disabled", "pod_required", "event_access_required"],
)
async def test_admin_quote_denies_unentitled_session_visits(monkeypatch, reason):
    session = SimpleNamespace(
        session_type="club", status="scheduled", id=uuid.uuid4(), cohort_id=None
    )
    evaluated = SimpleNamespace(
        sign_in_eligible=True,
        reason=reason,
        price_label=None,
        access_source=None,
        fee_amount_kobo=None,
        pricing_audience=None,
    )
    monkeypatch.setattr(
        bookings,
        "evaluate_member_session_access",
        AsyncMock(return_value=evaluated),
    )
    quote = await bookings._admin_member_admission_quote(
        session, uuid.uuid4(), SimpleNamespace()
    )
    assert not quote["allowed"]
    assert quote["fee_amount_kobo"] == 0
    assert quote["reason"]


@pytest.mark.asyncio
async def test_quote_does_not_admit_unsigned_academy_visitors(monkeypatch):
    from libs.common import service_client

    session = SimpleNamespace(
        session_type="cohort_class",
        status="scheduled",
        cohort_id=uuid.uuid4(),
        allows_guests=False,
        guest_fee_kobo=2000000,
        pool_fee=1000000,
    )
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"enrolled": False},
    )
    monkeypatch.setattr(
        service_client, "internal_get", AsyncMock(return_value=response)
    )
    result = await bookings._admin_member_admission_quote(
        session, uuid.uuid4(), SimpleNamespace()
    )
    assert not result["allowed"]
    assert result["pricing_audience"] == "guest"
