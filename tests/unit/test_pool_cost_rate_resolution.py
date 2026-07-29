"""Rate applicability and specificity tests for pool cost quotes."""

import uuid
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from services.pools_service.schemas.pricing import CostQuoteRequest
from services.pools_service.services.pricing import (
    PricingAmbiguityError,
    _choose_rate,
    _condition_score,
    _quantity,
    _rate_applies,
)


def _request(activity_scope="club"):
    return CostQuoteRequest(
        pool_id=uuid.uuid4(),
        activity_scope=activity_scope,
        starts_at=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        ends_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        timezone="Africa/Lagos",
        expected_attendees=20,
        expected_staff=2,
        lanes=3,
    )


def _rate(**overrides):
    values = {
        "id": uuid.uuid4(),
        "activity_scope": "all",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "day_of_week": None,
        "starts_after": None,
        "ends_before": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_activity_specific_rate_wins_over_all_activity_fallback():
    request = _request("club")
    fallback = _rate(activity_scope="all")
    club = _rate(activity_scope="club")

    selected = _choose_rate(
        [fallback, club],
        lambda rate: _condition_score(rate, request),
    )

    assert selected is club


def test_rate_checks_activity_day_and_time_band():
    request = _request("academy")
    saturday_morning = _rate(
        activity_scope="academy",
        day_of_week=5,
        starts_after=time(8),
        ends_before=time(11),
    )

    assert _rate_applies(
        saturday_morning,
        request,
        datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
    )
    assert not _rate_applies(
        saturday_morning,
        request,
        datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )


def test_equal_specificity_fails_instead_of_selecting_silently():
    request = _request()
    rates = [_rate(activity_scope="club"), _rate(activity_scope="club")]

    with pytest.raises(PricingAmbiguityError):
        _choose_rate(rates, lambda rate: _condition_score(rate, request))


def test_quote_quantities_use_the_correct_driver():
    request = _request()

    assert _quantity("per_attendee", request, 1) == 20
    assert _quantity("per_staff", request, 1) == 2
    assert _quantity("per_hour", request, 1) == 2
    assert _quantity("per_lane", request, 1) == 3
    assert _quantity("flat_session", request, 1) == 1
