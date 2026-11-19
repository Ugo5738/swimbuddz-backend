"""Regression checks for member Swim Plan policy and date/safety inputs."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.events_service.routers.swim_plans import (
    PlanCreate,
    VenueVerification,
    _initial_approval,
)


def _future(minutes=120):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def test_public_approval_defaults_to_approved_but_is_configurable():
    assert _initial_approval("open", True) == "approved"
    assert _initial_approval("open", False) == "pending"
    assert _initial_approval("solo", False) == "approved"


def test_public_plan_requires_pool_and_valid_duration():
    start = _future()
    with pytest.raises(ValidationError):
        PlanCreate(
            kind="open",
            title="Open swim",
            location="Unverified beach",
            starts_at=start.isoformat(),
            ends_at=(start + timedelta(hours=1)).isoformat(),
        )
    with pytest.raises(ValidationError):
        PlanCreate(
            kind="solo",
            title="My swim",
            location="Yaba",
            participants=2,
            starts_at=start.isoformat(),
            ends_at=(start + timedelta(hours=1)).isoformat(),
        )
    with pytest.raises(ValidationError):
        PlanCreate(
            kind="solo",
            title="My swim",
            location="Yaba",
            starts_at=start.isoformat(),
            ends_at=(start + timedelta(hours=10)).isoformat(),
        )


def test_verified_pool_requires_traceable_reference():
    with pytest.raises(ValidationError):
        VenueVerification(confirmed=True, reference="ok")
    assert VenueVerification(confirmed=True, reference="BOOK-123456").confirmed


def test_plan_rejects_timezone_naive_dates():
    start = _future().replace(tzinfo=None)
    with pytest.raises(ValidationError):
        PlanCreate(
            kind="solo",
            title="Personal swim",
            location="Yaba",
            starts_at=start.isoformat(),
            ends_at=(start + timedelta(hours=1)).isoformat(),
        )
