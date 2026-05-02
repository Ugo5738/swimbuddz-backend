"""
Integration tests for the flywheel-related internal endpoints on
members_service: ``GET /internal/members/joined-tier`` and
``GET /internal/members/{member_id}/tier-history``.

These endpoints are consumed by reporting_service.tasks.flywheel to compute
funnel-conversion snapshots (e.g. community -> club within 90 days).

Skeletons only — flesh out assertions / fixtures when running locally.
"""

import uuid
from datetime import datetime, timezone

import pytest

from tests.factories import MemberFactory


def _utc(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# /internal/members/joined-tier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_invalid_tier_400(members_client):
    """Unknown tier returns 400."""
    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "bogus", "from": "2026-01-01", "to": "2026-03-31"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_inverted_window_400(members_client):
    """from > to returns 400."""
    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "community", "from": "2026-04-01", "to": "2026-01-01"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_community_uses_created_at(members_client, db_session):
    """A community member created in-window is returned."""
    from services.members_service.models import (
        AcquisitionSource,
        MemberMembership,
        MemberProfile,
    )

    member = MemberFactory.create(created_at=_utc(2026, 2, 15))
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="community",
    )
    profile = MemberProfile(
        id=uuid.uuid4(),
        member_id=member.id,
        acquisition_source=AcquisitionSource.SOCIAL_INSTAGRAM,
    )
    db_session.add_all([member, membership, profile])
    await db_session.commit()

    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "community", "from": "2026-01-01", "to": "2026-03-31"},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [m["id"] for m in data["members"]]
    assert str(member.id) in ids
    found = next(m for m in data["members"] if m["id"] == str(member.id))
    assert found["acquisition_source"] == "social_instagram"
    assert found["source_joined_at"]  # ISO datetime string


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_community_excludes_out_of_window(members_client, db_session):
    """Members created outside the window are not returned."""
    from services.members_service.models import MemberMembership

    member = MemberFactory.create(created_at=_utc(2025, 11, 1))
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="community",
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "community", "from": "2026-01-01", "to": "2026-03-31"},
    )
    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["members"]]
    assert str(member.id) not in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_club_uses_paid_until(members_client, db_session):
    """A member whose club_paid_until lands in-window is returned for tier=club."""
    from services.members_service.models import MemberMembership

    member = MemberFactory.create()
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="club",
        club_paid_until=_utc(2026, 2, 20),
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "club", "from": "2026-01-01", "to": "2026-03-31"},
    )
    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["members"]]
    assert str(member.id) in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_joined_tier_acquisition_source_null_when_no_profile(
    members_client, db_session
):
    """Members without a MemberProfile return acquisition_source=null."""
    from services.members_service.models import MemberMembership

    member = MemberFactory.create(created_at=_utc(2026, 2, 5))
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="community",
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    response = await members_client.get(
        "/internal/members/joined-tier",
        params={"tier": "community", "from": "2026-01-01", "to": "2026-03-31"},
    )
    assert response.status_code == 200
    found = next(
        (m for m in response.json()["members"] if m["id"] == str(member.id)),
        None,
    )
    assert found is not None
    assert found["acquisition_source"] is None


# ---------------------------------------------------------------------------
# /internal/members/{member_id}/tier-history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tier_history_not_found(members_client):
    fake = uuid.uuid4()
    response = await members_client.get(f"/internal/members/{fake}/tier-history")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tier_history_community_only(members_client, db_session):
    """A pure-community member returns one community entry."""
    from services.members_service.models import MemberMembership

    member = MemberFactory.create(created_at=_utc(2026, 1, 5))
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="community",
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}/tier-history")
    assert response.status_code == 200
    entries = response.json()["entries"]
    tiers = [e["tier"] for e in entries]
    assert "community" in tiers
    community_entry = next(e for e in entries if e["tier"] == "community")
    assert community_entry["entered_at"]
    assert community_entry["exited_at"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tier_history_includes_club_when_paid_until_set(
    members_client, db_session
):
    """If club_paid_until is set, a club entry appears with that exited_at."""
    from services.members_service.models import MemberMembership

    member = MemberFactory.create(created_at=_utc(2026, 1, 5))
    club_until = _utc(2026, 6, 30)
    membership = MemberMembership(
        id=uuid.uuid4(),
        member_id=member.id,
        primary_tier="club",
        club_paid_until=club_until,
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}/tier-history")
    assert response.status_code == 200
    entries = response.json()["entries"]
    club_entry = next((e for e in entries if e["tier"] == "club"), None)
    assert club_entry is not None
    assert club_entry["exited_at"] is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_tier_history_no_membership_returns_implicit_community(
    members_client, db_session
):
    """A member with no MemberMembership row still has an implicit community entry."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}/tier-history")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["tier"] == "community"
