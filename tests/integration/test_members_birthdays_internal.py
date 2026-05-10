"""Integration tests for /internal/members/birthdays-today and /internal/members/admins."""

from datetime import datetime, timezone

import pytest
from tests.factories import MemberFactory


def _profile(member_id, *, dob: datetime | None):
    """Helper: build a minimal MemberProfile row tied to ``member_id``."""
    from services.members_service.models import MemberProfile

    return MemberProfile(
        member_id=member_id,
        date_of_birth=dob,
    )


# ---------------------------------------------------------------------------
# /internal/members/birthdays-today
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_returns_match(members_client, db_session):
    """Active approved member with a DOB matching `on=` is returned with age."""
    m = MemberFactory.create(first_name="Ada", last_name="Lovelace")
    db_session.add(m)
    await db_session.flush()
    db_session.add(_profile(m.id, dob=datetime(1990, 6, 15, tzinfo=timezone.utc)))
    await db_session.commit()

    resp = await members_client.get(
        "/internal/members/birthdays-today",
        params={"on": "2026-06-15"},
    )

    assert resp.status_code == 200
    data = resp.json()
    matches = [r for r in data if r["id"] == str(m.id)]
    assert len(matches) == 1
    assert matches[0]["first_name"] == "Ada"
    assert matches[0]["age"] == 36


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_excludes_non_matching_dob(members_client, db_session):
    """Members whose DOB doesn't fall on the target date aren't returned."""
    m = MemberFactory.create()
    db_session.add(m)
    await db_session.flush()
    db_session.add(_profile(m.id, dob=datetime(1990, 6, 15, tzinfo=timezone.utc)))
    await db_session.commit()

    resp = await members_client.get(
        "/internal/members/birthdays-today",
        params={"on": "2026-06-16"},  # one day off
    )
    assert resp.status_code == 200
    assert all(r["id"] != str(m.id) for r in resp.json())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_excludes_inactive(members_client, db_session):
    """is_active=False members are excluded from results."""
    m = MemberFactory.create(is_active=False)
    db_session.add(m)
    await db_session.flush()
    db_session.add(_profile(m.id, dob=datetime(1990, 7, 4, tzinfo=timezone.utc)))
    await db_session.commit()

    resp = await members_client.get(
        "/internal/members/birthdays-today",
        params={"on": "2026-07-04"},
    )
    assert resp.status_code == 200
    assert all(r["id"] != str(m.id) for r in resp.json())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_excludes_unapproved(members_client, db_session):
    """approval_status != 'approved' is excluded — pending/rejected don't get celebrated."""
    m = MemberFactory.create(approval_status="pending")
    db_session.add(m)
    await db_session.flush()
    db_session.add(_profile(m.id, dob=datetime(1990, 8, 12, tzinfo=timezone.utc)))
    await db_session.commit()

    resp = await members_client.get(
        "/internal/members/birthdays-today",
        params={"on": "2026-08-12"},
    )
    assert resp.status_code == 200
    assert all(r["id"] != str(m.id) for r in resp.json())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_skips_members_without_dob(members_client, db_session):
    """Members with no profile or null DOB aren't returned."""
    m_no_profile = MemberFactory.create()
    m_null_dob = MemberFactory.create()
    db_session.add_all([m_no_profile, m_null_dob])
    await db_session.flush()
    # Only one of them has a profile (with null DOB)
    db_session.add(_profile(m_null_dob.id, dob=None))
    await db_session.commit()

    resp = await members_client.get(
        "/internal/members/birthdays-today",
        params={"on": "2026-09-10"},
    )
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(m_no_profile.id) not in ids
    assert str(m_null_dob.id) not in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_birthdays_today_default_on_is_today_in_lagos(members_client, db_session):
    """Without `on=`, the endpoint resolves 'today' in Africa/Lagos.

    Black-box check: it returns 200 and a list (we can't assert specific
    members without freezing time, but the endpoint must accept the call).
    """
    resp = await members_client.get("/internal/members/birthdays-today")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# /internal/members/admins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admins_returns_admin_role(members_client, db_session):
    admin = MemberFactory.create(roles=["admin", "member"])
    db_session.add(admin)
    await db_session.commit()

    resp = await members_client.get("/internal/members/admins")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(admin.id) in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admins_returns_comms_admin_role(members_client, db_session):
    """`comms_admin` role is also picked up by the admin lookup."""
    member = MemberFactory.create(roles=["comms_admin", "member"])
    db_session.add(member)
    await db_session.commit()

    resp = await members_client.get("/internal/members/admins")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(member.id) in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admins_returns_community_manager_role(members_client, db_session):
    member = MemberFactory.create(roles=["community_manager", "member"])
    db_session.add(member)
    await db_session.commit()

    resp = await members_client.get("/internal/members/admins")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(member.id) in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admins_excludes_plain_members(members_client, db_session):
    """A regular member with only `member` role is not returned."""
    plain = MemberFactory.create(roles=["member"])
    db_session.add(plain)
    await db_session.commit()

    resp = await members_client.get("/internal/members/admins")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(plain.id) not in ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admins_excludes_inactive(members_client, db_session):
    """Inactive admins aren't included in the daily reminder fan-out."""
    admin = MemberFactory.create(roles=["admin"], is_active=False)
    db_session.add(admin)
    await db_session.commit()

    resp = await members_client.get("/internal/members/admins")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()}
    assert str(admin.id) not in ids
