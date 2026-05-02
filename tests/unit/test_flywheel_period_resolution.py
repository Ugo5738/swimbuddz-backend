"""Unit tests for flywheel period-resolution math.

These don't touch the database — pure functions in
``services.reporting_service.tasks.flywheel`` and
``services.reporting_service.routers.admin_flywheel``.
"""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from services.reporting_service.tasks.flywheel import (
    _parse_iso_dt,
    _resolve_period,
)


# ---------------------------------------------------------------------------
# _resolve_period
# ---------------------------------------------------------------------------


def test_resolve_period_explicit_q1():
    """Explicit '2026-Q1' resolves to Jan 1 – Mar 31, 2026."""
    label, start, end = _resolve_period("2026-Q1")
    assert label == "2026-Q1"
    assert start == date(2026, 1, 1)
    assert end == date(2026, 3, 31)


def test_resolve_period_explicit_q4():
    """Explicit '2025-Q4' resolves to Oct 1 – Dec 31, 2025."""
    label, start, end = _resolve_period("2025-Q4")
    assert label == "2025-Q4"
    assert start == date(2025, 10, 1)
    assert end == date(2025, 12, 31)


def test_resolve_period_explicit_q2():
    """Q2 spans Apr 1 – Jun 30, including the leap day handling."""
    label, start, end = _resolve_period("2024-Q2")
    assert label == "2024-Q2"
    assert start == date(2024, 4, 1)
    assert end == date(2024, 6, 30)


def test_resolve_period_explicit_q3():
    """Q3 spans Jul 1 – Sep 30."""
    label, start, end = _resolve_period("2026-Q3")
    assert label == "2026-Q3"
    assert start == date(2026, 7, 1)
    assert end == date(2026, 9, 30)


def test_resolve_period_default_in_q2_returns_q1():
    """When today is in Q2, the most recently completed quarter is Q1."""
    fake_now = datetime(2026, 5, 15, 0, 0, 0, tzinfo=timezone.utc)
    with patch(
        "services.reporting_service.tasks.flywheel.utc_now",
        return_value=fake_now,
    ):
        label, start, end = _resolve_period(None)
    assert label == "2026-Q1"
    assert start == date(2026, 1, 1)
    assert end == date(2026, 3, 31)


def test_resolve_period_default_in_q1_rolls_back_to_prior_q4():
    """When today is in Q1, the most recently completed quarter is the prior year's Q4."""
    fake_now = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    with patch(
        "services.reporting_service.tasks.flywheel.utc_now",
        return_value=fake_now,
    ):
        label, start, end = _resolve_period(None)
    assert label == "2025-Q4"
    assert start == date(2025, 10, 1)
    assert end == date(2025, 12, 31)


def test_resolve_period_default_first_day_of_q2_returns_q1():
    """Edge case: April 1 (start of Q2) — most recent completed is Q1."""
    fake_now = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    with patch(
        "services.reporting_service.tasks.flywheel.utc_now",
        return_value=fake_now,
    ):
        label, _, _ = _resolve_period(None)
    assert label == "2026-Q1"


# ---------------------------------------------------------------------------
# _parse_iso_dt
# ---------------------------------------------------------------------------


def test_parse_iso_dt_returns_none_for_falsy():
    assert _parse_iso_dt(None) is None
    assert _parse_iso_dt("") is None
    assert _parse_iso_dt(0) is None


def test_parse_iso_dt_passes_through_datetime():
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_iso_dt(dt) is dt


def test_parse_iso_dt_handles_z_suffix():
    """ISO strings ending in Z should be treated as UTC."""
    parsed = _parse_iso_dt("2026-04-29T10:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026
    assert parsed.month == 4
    assert parsed.day == 29


def test_parse_iso_dt_handles_offset_suffix():
    parsed = _parse_iso_dt("2026-04-29T10:00:00+01:00")
    assert parsed is not None
    assert parsed.hour == 10


def test_parse_iso_dt_returns_none_for_garbage():
    assert _parse_iso_dt("not-a-date") is None
    assert _parse_iso_dt("2026-13-45") is None
