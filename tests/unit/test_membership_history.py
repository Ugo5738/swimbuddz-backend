from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.members_service.routers.members import me


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


async def _history(monkeypatch, *, legacy_end=None, enrollments=()):
    member = SimpleNamespace(
        id=uuid4(),
        created_at=NOW - timedelta(days=365),
        membership=SimpleNamespace(
            community_paid_until=NOW + timedelta(days=365),
            club_paid_until=legacy_end,
            club_billing_cycle_months=3,
            post_academy_club_until=None,
            declared_tiers=["community", "club"],
        ),
    )
    rows = [
        (enrollment, SimpleNamespace(name="Yaba Club")) for enrollment in enrollments
    ]
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: member),
                SimpleNamespace(all=lambda: rows),
            ]
        )
    )
    monkeypatch.setattr(me, "utc_now", lambda: NOW)
    return await me.get_current_member_membership_history(
        current_user=SimpleNamespace(user_id="member-auth"),
        db=db,
    )


def _enrollment(start, end, status="active"):
    return SimpleNamespace(
        id=uuid4(),
        starts_at=start,
        ends_at=end,
        status=status,
        payment_mode="quarterly_prepaid",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,status,legacy_visible",
    [
        (30, "active", False),  # Matching expiry is already represented.
        (-30, "active", True),  # Older, separate legacy entitlement remains.
        (-2, "active", True),  # Adjacent periods are separate, too.
        (30, "revoked", True),  # Revoked enrollment does not replace valid history.
    ],
)
async def test_exact_history_deduplication_preserves_real_legacy_periods(
    monkeypatch,
    offset,
    status,
    legacy_visible,
):
    result = await _history(
        monkeypatch,
        legacy_end=NOW + timedelta(days=offset),
        enrollments=[
            _enrollment(NOW - timedelta(days=2), NOW + timedelta(days=30), status)
        ],
    )
    assert (
        any(p.id == "legacy-club-membership" for p in result.periods) is legacy_visible
    )


@pytest.mark.asyncio
async def test_later_prepaid_quarter_does_not_hide_current_renewal_gap(monkeypatch):
    current_end = NOW + timedelta(days=30)
    result = await _history(
        monkeypatch,
        enrollments=[
            _enrollment(NOW - timedelta(days=30), current_end),
            _enrollment(NOW + timedelta(days=90), NOW + timedelta(days=180)),
        ],
    )
    assert result.club_renewal_status == "active"
    assert result.club_renewal_due_at == current_end - timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_adjacent_prepaid_quarter_extends_renewal_date(monkeypatch):
    current_end = NOW + timedelta(days=30)
    next_end = NOW + timedelta(days=120)
    result = await _history(
        monkeypatch,
        enrollments=[
            _enrollment(NOW - timedelta(days=30), current_end),
            _enrollment(current_end, next_end),
        ],
    )
    assert result.club_renewal_due_at == next_end - timedelta(microseconds=1)
