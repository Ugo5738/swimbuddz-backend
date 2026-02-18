"""
Contract tests for members service internal endpoints.

These tests validate that the response SHAPE matches what consuming
services expect. They don't test business logic — they test that
the JSON keys and types are correct.

If these tests break, it means a consuming service will break too.
"""

import pytest
from tests.factories import CoachBankAccountFactory, CoachProfileFactory, MemberFactory


@pytest.mark.asyncio
@pytest.mark.contract
async def test_member_by_id_contract(members_client, db_session):
    """
    GET /internal/members/{id} response contains all fields that
    service_client.get_member_by_id() consumers depend on.

    Consumers: academy_service, communications_service, payments_service
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}")
    assert response.status_code == 200
    data = response.json()

    # MemberBasic fields consumed by other services
    required_fields = ["id", "first_name", "last_name", "email"]
    for field in required_fields:
        assert field in data, (
            f"Missing required contract field '{field}' in /internal/members/{{id}} response. "
            f"This field is used by academy, communications, and payments services."
        )

    # Type checks
    assert isinstance(data["id"], str)
    assert isinstance(data["email"], str)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_member_by_auth_id_contract(members_client, db_session):
    """
    GET /internal/members/by-auth/{auth_id} response contract.

    Consumers: attendance_service, sessions_service
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(f"/internal/members/by-auth/{member.auth_id}")
    assert response.status_code == 200
    data = response.json()

    required_fields = ["id", "first_name", "last_name", "email"]
    for field in required_fields:
        assert (
            field in data
        ), f"Missing contract field '{field}' in /internal/members/by-auth response."


@pytest.mark.asyncio
@pytest.mark.contract
async def test_bulk_members_contract(members_client, db_session):
    """
    POST /internal/members/bulk response contract.

    Consumers: academy_service (cohort roster display)
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.post(
        "/internal/members/bulk",
        json={"ids": [str(member.id)]},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    item = data[0]
    required_fields = ["id", "first_name", "last_name", "email"]
    for field in required_fields:
        assert (
            field in item
        ), f"Missing contract field '{field}' in bulk members response item."


@pytest.mark.asyncio
@pytest.mark.contract
async def test_coach_profile_contract(members_client, db_session):
    """
    GET /internal/coaches/{id}/profile response contract.

    Consumers: academy_service (coach assignment), ai_service (scoring)
    """
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(f"/internal/coaches/{member.id}/profile")
    assert response.status_code == 200
    data = response.json()

    required_fields = ["member_id", "status"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in coach profile response. "
            f"Used by academy_service for coach assignment."
        )

    assert isinstance(data["member_id"], str)
    assert isinstance(data["status"], str)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_coach_readiness_contract(members_client, db_session):
    """
    GET /internal/coaches/{id}/readiness response contract.

    Consumers: ai_service (complexity scoring, coach suggestion)
    """
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(f"/internal/coaches/{member.id}/readiness")
    assert response.status_code == 200
    data = response.json()

    required_fields = [
        "profile_id",
        "total_coaching_hours",
        "has_cpr_training",
        "has_active_agreement",
    ]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in coach readiness response. "
            f"Used by ai_service for complexity scoring."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_bank_account_contract(members_client, db_session):
    """
    GET /internal/members/{id}/bank-account response contract.

    Consumers: payments_service (coach payout)
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.flush()

    bank = CoachBankAccountFactory.create(member_id=member.id)
    db_session.add(bank)
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}/bank-account")
    assert response.status_code == 200
    data = response.json()

    required_fields = ["bank_code", "account_number", "account_name", "is_verified"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in bank account response. "
            f"Used by payments_service for coach payouts."
        )
