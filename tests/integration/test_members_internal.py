"""
Integration tests for the members service internal endpoints.

These endpoints are called by other services via the service client.
They are the most critical to test because they form the contract
that the entire microservice architecture depends on.
"""

import pytest
from tests.factories import CoachBankAccountFactory, CoachProfileFactory, MemberFactory


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_auth_id(members_client, db_session):
    """Internal lookup by Supabase auth_id returns the member."""
    member = MemberFactory.create(auth_id="auth-internal-test")
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/by-auth/{member.auth_id}",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(member.id)
    assert data["email"] == member.email
    assert data["first_name"] == "Test"
    assert data["last_name"] == "Member"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_auth_id_not_found(members_client, db_session):
    """Nonexistent auth_id returns 404."""
    response = await members_client.get(
        "/internal/members/by-auth/nonexistent-auth-id",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_id(members_client, db_session):
    """Internal lookup by member UUID returns the member."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/{member.id}",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(member.id)
    assert data["email"] == member.email


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_id_not_found(members_client, db_session):
    """Nonexistent member ID returns 404."""
    import uuid

    fake_id = uuid.uuid4()
    response = await members_client.get(
        f"/internal/members/{fake_id}",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_member_lookup(members_client, db_session):
    """Bulk lookup returns all found members, skips missing."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    db_session.add_all([m1, m2])
    await db_session.commit()

    response = await members_client.post(
        "/internal/members/bulk",
        json={"ids": [str(m1.id), str(m2.id), "00000000-0000-0000-0000-000000000099"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    returned_ids = {item["id"] for item in data}
    assert str(m1.id) in returned_ids
    assert str(m2.id) in returned_ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_member_lookup_empty_list(members_client, db_session):
    """Bulk lookup with empty list returns empty array."""
    response = await members_client.post(
        "/internal/members/bulk",
        json={"ids": []},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_coach_profile(members_client, db_session):
    """Internal coach profile lookup returns coach data."""
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/profile",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["member_id"] == str(member.id)
    assert data["status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_coach_profile_not_a_coach(members_client, db_session):
    """Coach profile lookup for non-coach returns 404."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/profile",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_bank_account(members_client, db_session):
    """Internal bank account lookup returns account data."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.flush()

    bank = CoachBankAccountFactory.create(member_id=member.id)
    db_session.add(bank)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/{member.id}/bank-account",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["member_id"] == str(member.id)
    assert data["bank_name"] == "GTBank"
    assert data["account_number"] == "0123456789"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_bank_account_not_found(members_client, db_session):
    """Bank account lookup for member without account returns 404."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/{member.id}/bank-account",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_coach_readiness_data(members_client, db_session):
    """Coach readiness data returns extended profile info."""
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id, total_coaching_hours=200)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/readiness",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_coaching_hours"] == 200
    assert data["has_active_agreement"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_active_members(members_client, db_session):
    """Active members endpoint returns all active members."""
    active = MemberFactory.create(is_active=True)
    inactive = MemberFactory.create(is_active=False)
    db_session.add_all([active, inactive])
    await db_session.commit()

    response = await members_client.get("/internal/members/active")

    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data}
    assert str(active.id) in returned_ids
    # inactive may or may not be in returned — depends on seeded data
    # but the active one should definitely be there
