"""Admission/lifecycle invariants independent of payment providers."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.sessions_service.schemas.guest_pass import GuestPassCreate
from services.sessions_service.services import guest_booking as policy

NOW = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)


def swim(**overrides):
    values = dict(
        id=uuid.uuid4(),
        status="scheduled",
        allows_guests=True,
        guest_booking_mode="public",
        guest_fee_kobo=500000,
        starts_at=NOW + timedelta(hours=2),
        ends_at=NOW + timedelta(hours=4),
        guest_booking_closes_at=None,
        guest_reconciliation_days=3,
        guest_location_private=False,
        event_id=None,
        location_name="Private pool",
        location_address="Exact address",
    )
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("mode", ["public", "member_invite", "approval_required"])
def test_enabled_modes_use_reservation_before_start(mode):
    assert (
        policy.lifecycle_mode(swim(guest_booking_mode=mode), now=NOW) == "reservation"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"allows_guests": False},
        {"guest_booking_mode": "disabled"},
        {"guest_fee_kobo": None},
        {"status": "draft"},
        {"status": "cancelled"},
        {"guest_booking_closes_at": NOW},
    ],
)
def test_closed_states_cannot_be_bypassed_with_a_grant(overrides):
    assert policy.lifecycle_mode(swim(**overrides), now=NOW, has_grant=True) == "closed"


def test_explicit_zero_is_free_and_does_not_fall_back_to_member_price():
    assert policy.lifecycle_mode(swim(guest_fee_kobo=0), now=NOW) == "reservation"


def test_start_boundary_switches_to_settlement_and_then_closes():
    session = swim()
    assert policy.lifecycle_mode(session, now=session.starts_at) == "settlement"
    assert (
        policy.lifecycle_mode(session, now=session.ends_at + timedelta(days=3))
        == "settlement"
    )
    later = session.ends_at + timedelta(days=3, seconds=1)
    assert policy.lifecycle_mode(session, now=later) == "closed"
    assert policy.lifecycle_mode(session, now=later, has_grant=True) == "settlement"


def test_zero_reconciliation_days_requires_admin_link_at_start():
    session = swim(guest_reconciliation_days=0)
    assert policy.lifecycle_mode(session, now=session.starts_at) == "closed"
    assert (
        policy.lifecycle_mode(session, now=session.starts_at, has_grant=True)
        == "settlement"
    )


def test_member_invite_and_approval_gate_are_separate_from_acquisition(monkeypatch):
    monkeypatch.setattr(policy, "utc_now", lambda: NOW)
    with pytest.raises(HTTPException, match="member invitation"):
        policy.require_admission(
            swim(guest_booking_mode="member_invite"),
            member_invitation=None,
            grant=None,
            email="guest@example.com",
        )
    assert (
        policy.require_admission(
            swim(guest_booking_mode="member_invite"),
            member_invitation=SimpleNamespace(id=uuid.uuid4()),
            grant=None,
            email="guest@example.com",
        )
        == "reservation"
    )
    with pytest.raises(HTTPException, match="approval link"):
        policy.require_admission(
            swim(guest_booking_mode="approval_required"),
            member_invitation=SimpleNamespace(id=uuid.uuid4()),
            grant=None,
            email="guest@example.com",
        )
    grant = SimpleNamespace(email="guest@example.com")
    assert (
        policy.require_admission(
            swim(guest_booking_mode="approval_required"),
            member_invitation=None,
            grant=grant,
            email="guest@example.com",
        )
        == "reservation"
    )
    with pytest.raises(HTTPException, match="email address"):
        policy.require_admission(
            swim(),
            member_invitation=None,
            grant=grant,
            email="someone-else@example.com",
        )


def test_receipt_token_is_bound_to_one_pass():
    first, second = uuid.uuid4(), uuid.uuid4()
    token = policy.receipt_token(first)
    assert policy.has_receipt_access(first, token)
    assert not policy.has_receipt_access(second, token)
    assert not policy.has_receipt_access(first, None)
    assert not policy.has_receipt_access(first, "é" * 64)


def test_private_venue_only_revealed_for_confirmed_private_receipt():
    session = swim(guest_location_private=True)
    assert policy.public_location(session, {}) == (None, None)
    assert policy.public_location(session, {}, confirmed_private=True) == (
        "Private pool",
        "Exact address",
    )
    assert policy.public_location(
        swim(), {"is_location_private": True, "location_area": "Yaba"}
    ) == ("Yaba", None)


@pytest.mark.asyncio
async def test_private_event_requires_individual_approval(monkeypatch):
    monkeypatch.setattr(
        policy,
        "get_event_session_contract",
        AsyncMock(
            return_value={
                "visibility": "invite_only",
                "tier_access": "invite_only",
                "status": "published",
            }
        ),
    )
    session = swim(event_id=uuid.uuid4())
    with pytest.raises(HTTPException, match="individual guest approval"):
        await policy.event_guest_context(session)
    assert await policy.event_guest_context(session, has_grant=True)


def test_safety_acknowledgement_is_required_and_marketing_optional():
    data = dict(
        full_name="Ada Guest",
        email="ada@example.com",
        phone="08012345678",
        waiver_accepted=True,
    )
    assert GuestPassCreate(**data).marketing_consent is False
    with pytest.raises(ValidationError, match="safety acknowledgement"):
        GuestPassCreate(**(data | {"waiver_accepted": False}))
    with pytest.raises(ValidationError, match="Guardian"):
        GuestPassCreate(**(data | {"date_of_birth": "2020-01-01"}))
    with pytest.raises(ValidationError, match="future"):
        GuestPassCreate(**(data | {"date_of_birth": "2099-01-01"}))
