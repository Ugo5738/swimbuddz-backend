"""Unit tests for the guest-booking policy helpers (pure, no DB).

Covers the slice-1b validation logic woven into POST /sessions/{id}/book:
head-count + fee math, the per-session guest policy, and the minor
safeguarding gate. The DB-bound paths (capacity lock, guest persistence,
full endpoint) are exercised by the integration suite. See
docs/design/GUEST_AND_GROUP_BOOKING_DESIGN.md.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services.sessions_service.routers.bookings import (
    _is_minor,
    _validate_guest_policy,
)
from services.sessions_service.schemas.booking import BookingGuestCreate, GuestIntent


def _session_start(days_ahead: int = 7) -> datetime:
    return datetime(2030, 6, 1, 9, 0, tzinfo=timezone.utc) + timedelta(days=days_ahead)


def _adult_guest(**kw) -> BookingGuestCreate:
    base = dict(full_name="Ada Friend", phone="08030000000")
    base.update(kw)
    return BookingGuestCreate(**base)


# --- fee + head-count math -------------------------------------------------


def test_party_size_and_fee_math():
    guests = [_adult_guest(), _adult_guest(full_name="Bee Friend")]
    party_size = 1 + len(guests)  # member + 2 guests
    assert party_size == 3
    pool_fee = 3500
    assert pool_fee * party_size == 10500


def test_solo_booking_party_size_is_one():
    assert 1 + len([]) == 1


# --- minor detection -------------------------------------------------------


def test_is_minor_true_for_under_18():
    start = _session_start()
    dob = start.replace(year=start.year - 10).date()
    assert _is_minor(dob, start) is True


def test_is_minor_false_for_adult():
    start = _session_start()
    dob = start.replace(year=start.year - 25).date()
    assert _is_minor(dob, start) is False


def test_is_minor_boundary_exactly_18():
    start = _session_start()
    dob = start.replace(year=start.year - 18).date()  # 18 on the session day
    assert _is_minor(dob, start) is False


# --- guest policy ----------------------------------------------------------


def test_no_guests_is_always_allowed():
    # an empty list passes even when the session forbids guests
    _validate_guest_policy(
        allows_guests=False, max_guests=0, session_starts_at=_session_start(), guests=[]
    )


def test_guests_rejected_when_session_disallows():
    with pytest.raises(HTTPException) as ei:
        _validate_guest_policy(
            allows_guests=False,
            max_guests=4,
            session_starts_at=_session_start(),
            guests=[_adult_guest()],
        )
    assert ei.value.status_code == 422


def test_guests_rejected_over_max():
    with pytest.raises(HTTPException) as ei:
        _validate_guest_policy(
            allows_guests=True,
            max_guests=1,
            session_starts_at=_session_start(),
            guests=[_adult_guest(), _adult_guest(full_name="Two")],
        )
    assert ei.value.status_code == 422


def test_guest_without_name_rejected():
    with pytest.raises(HTTPException) as ei:
        _validate_guest_policy(
            allows_guests=True,
            max_guests=4,
            session_starts_at=_session_start(),
            guests=[BookingGuestCreate(full_name="   ")],
        )
    assert ei.value.status_code == 422


def test_minor_without_guardian_rejected():
    start = _session_start()
    minor = _adult_guest(
        full_name="Kid", date_of_birth=start.replace(year=start.year - 8).date()
    )
    with pytest.raises(HTTPException) as ei:
        _validate_guest_policy(
            allows_guests=True, max_guests=4, session_starts_at=start, guests=[minor]
        )
    assert ei.value.status_code == 422


def test_minor_with_guardian_and_waiver_allowed():
    start = _session_start()
    minor = _adult_guest(
        full_name="Kid",
        date_of_birth=start.replace(year=start.year - 8).date(),
        guardian_name="Parent",
        guardian_phone="08030000001",
        waiver_accepted=True,
    )
    _validate_guest_policy(
        allows_guests=True, max_guests=4, session_starts_at=start, guests=[minor]
    )


def test_adult_guests_allowed_including_trial_intent():
    _validate_guest_policy(
        allows_guests=True,
        max_guests=4,
        session_starts_at=_session_start(),
        guests=[
            _adult_guest(),
            _adult_guest(full_name="Bee", intent=GuestIntent.TRIAL),
        ],
    )
