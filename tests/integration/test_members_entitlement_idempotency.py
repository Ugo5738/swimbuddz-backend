from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from libs.common.datetime_utils import utc_now
from services.members_service.models import (
    MemberEntitlementApplication,
    MemberMembership,
)
from tests.factories import MemberFactory


def parse_datetime(value: str) -> datetime:
    """Parse either an ISO +00:00 timestamp or a UTC Z timestamp."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_community_activation_replay_does_not_extend_twice(
    members_client,
    db_session,
    monkeypatch,
):
    member = MemberFactory.create(auth_id="entitlement-idempotency-member")
    original_expiry = utc_now() + timedelta(days=30)
    membership = MemberMembership(
        member_id=member.id,
        primary_tier="community",
        active_tiers=["community"],
        community_paid_until=original_expiry,
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    wallet_effect = AsyncMock()
    monkeypatch.setattr(
        "services.members_service.routers.admin.community._apply_wallet_paid_activation_side_effects",
        wallet_effect,
    )
    payload = {
        "years": 1,
        "idempotency_key": "payment:test-payment:community-activate",
        "source_reference": "SBZ-TEST-001",
    }

    first = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/community/activate",
        json=payload,
    )
    second = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/community/activate",
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert (
        second.json()["membership"]["community_paid_until"]
        == first.json()["membership"]["community_paid_until"]
    )
    application_count = await db_session.scalar(
        select(func.count(MemberEntitlementApplication.id)).where(
            MemberEntitlementApplication.idempotency_key == payload["idempotency_key"]
        )
    )
    assert application_count == 1
    wallet_effect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_entitlement_key_cannot_be_reused_for_another_action(
    members_client,
    db_session,
    monkeypatch,
):
    member = MemberFactory.create(auth_id="entitlement-conflict-member")
    membership = MemberMembership(
        member_id=member.id,
        primary_tier="community",
        active_tiers=["community"],
        community_paid_until=utc_now() + timedelta(days=30),
    )
    db_session.add_all([member, membership])
    await db_session.commit()
    monkeypatch.setattr(
        "services.members_service.routers.admin.community._apply_wallet_paid_activation_side_effects",
        AsyncMock(),
    )
    key = "payment:test-payment:shared-key"

    activated = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/community/activate",
        json={"years": 1, "idempotency_key": key},
    )
    conflict = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/community/extend",
        json={"months": 1, "idempotency_key": key},
    )

    assert activated.status_code == 200
    assert conflict.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_academy_activation_replay_does_not_regrant_bundled_periods(
    members_client,
    db_session,
):
    member = MemberFactory.create(auth_id="academy-entitlement-idempotency-member")
    db_session.add(member)
    await db_session.commit()

    cohort_end = utc_now() + timedelta(days=90)
    payload = {
        "cohort_end_date": cohort_end.isoformat(),
        "idempotency_key": "academy:test-enrollment:paid-access",
        "source_reference": "test-enrollment",
    }
    first = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/academy/activate",
        json=payload,
    )
    second = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/academy/activate",
        json=payload,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_membership = first.json()["membership"]
    second_membership = second.json()["membership"]
    assert (
        second_membership["academy_paid_until"]
        == first_membership["academy_paid_until"]
    )
    assert (
        second_membership["community_paid_until"]
        == first_membership["community_paid_until"]
    )
    assert second_membership["club_paid_until"] == first_membership["club_paid_until"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_academy_projection_can_shorten_without_revoking_lower_tiers(
    members_client,
    db_session,
):
    member = MemberFactory.create(auth_id="academy-exact-projection-member")
    membership = MemberMembership(
        member_id=member.id,
        primary_tier="academy",
        active_tiers=["academy", "club", "community"],
        academy_paid_until=utc_now() + timedelta(days=120),
        club_paid_until=utc_now() + timedelta(days=60),
        community_paid_until=utc_now() + timedelta(days=365),
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    shorter_end = utc_now() + timedelta(days=30)
    response = await members_client.post(
        f"/admin/members/by-auth/{member.auth_id}/academy/project",
        json={
            "paid_until": shorter_end.isoformat(),
            "source_reference": "test-withdrawal",
        },
    )

    assert response.status_code == 200, response.text
    projected = response.json()["membership"]

    assert parse_datetime(projected["academy_paid_until"]) == shorter_end
    assert parse_datetime(projected["club_paid_until"]) == membership.club_paid_until
    assert (
        parse_datetime(projected["community_paid_until"])
        == membership.community_paid_until
    )
