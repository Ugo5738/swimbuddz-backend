"""Integration tests for volunteer_service — the slot-claim + reward guards.

Volunteer slots are the closest thing this service has to a money path:
a claimed-then-completed slot becomes logged hours, hours drive tier
promotion, and tier promotion mints `VolunteerReward` perks
(discounted sessions, membership discounts). So the guards that decide
*who may claim what* and *whether a perk can be redeemed twice* are the
ones worth pinning.

auth_id → member_id resolution is a cross-service HTTP call to
members_service; we patch it at each submodule's import site (the
"patch where it's called from" rule — MEMORY.md). The resolved
member_id is then used to seed `VolunteerProfile` directly so the
in-process DB and the faked lookup agree.

Scope:
  - claim_slot: 404 unknown opp, 400 not-open, 400 slots full,
    400 no profile, 403 tier too low, 409 double-claim, happy path
    (OPEN_CLAIM auto-approves + increments slots_filled)
  - cancel_my_claim: happy path frees the slot; 404 when none
  - redeem_reward: 404 unknown, 409 double-redeem, 422 expired,
    happy path
  - register_as_volunteer: 201 happy, 409 already registered

Not in scope (follow-up): admin hours entry + reward grant, tier
auto-promotion math, QR self-checkin, the reliability-score sweep.
"""

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

# Patch the local reference in each submodule that resolves the member —
# the import has already copied the function object into that namespace.
_SLOTS = "services.volunteer_service.routers.member.slots.get_member_by_auth_id"
_PROFILE = "services.volunteer_service.routers.member.profile.get_member_by_auth_id"
_REWARDS = "services.volunteer_service.routers.member.rewards.get_member_by_auth_id"


@contextmanager
def _as_member(member_id: str):
    """Fake get_member_by_auth_id everywhere the member router calls it."""
    m = AsyncMock(return_value={"id": member_id})
    with patch(_SLOTS, m), patch(_PROFILE, m), patch(_REWARDS, m):
        yield member_id


async def _seed_member(db):
    """Insert a real members row so volunteer_* FKs to members.id resolve.

    volunteer_profiles / slots / rewards all FK members.id (the shared
    members table — not a cross-service-DB violation; volunteer_service
    soft-references it via MemberRef). Use the maintained MemberFactory
    so every NOT NULL column stays satisfied as the member schema moves.
    """
    from tests.factories import MemberFactory

    m = MemberFactory.create()
    db.add(m)
    await db.commit()
    return m.id


# ---------------------------------------------------------------------------
# Local factories (mirrors test_store_cart's in-file style).
# ---------------------------------------------------------------------------


def _make_profile(member_id, **overrides):
    from services.volunteer_service.models import VolunteerProfile, VolunteerTier

    d = {
        "id": uuid.uuid4(),
        "member_id": member_id,
        "tier": VolunteerTier.TIER_1,
        "is_active": True,
    }
    d.update(overrides)
    return VolunteerProfile(**d)


def _make_opportunity(**overrides):
    from services.volunteer_service.models import (
        OpportunityStatus,
        OpportunityType,
        VolunteerOpportunity,
        VolunteerTier,
    )

    s = uuid.uuid4().hex[:6]
    d = {
        "id": uuid.uuid4(),
        "title": f"Lane marshal {s}",
        "date": date.today() + timedelta(days=7),
        "slots_needed": 2,
        "slots_filled": 0,
        "opportunity_type": OpportunityType.OPEN_CLAIM,
        "status": OpportunityStatus.OPEN,
        "min_tier": VolunteerTier.TIER_1,
        "cancellation_deadline_hours": 24,
    }
    d.update(overrides)
    return VolunteerOpportunity(**d)


def _make_reward(member_id, **overrides):
    from services.volunteer_service.models import RewardType, VolunteerReward

    d = {
        "id": uuid.uuid4(),
        "member_id": member_id,
        "reward_type": RewardType.DISCOUNTED_SESSION,
        "title": "20% off your next session",
        "is_redeemed": False,
    }
    d.update(overrides)
    return VolunteerReward(**d)


# ---------------------------------------------------------------------------
# claim_slot guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_unknown_opportunity_404(volunteer_client):
    with _as_member(str(uuid.uuid4())):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{uuid.uuid4()}/claim"
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_requires_volunteer_profile_400(volunteer_client, db_session):
    """A member with no VolunteerProfile cannot claim — 400, not 500."""
    opp = _make_opportunity()
    db_session.add(opp)
    await db_session.commit()
    with _as_member(str(uuid.uuid4())):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert resp.status_code == 400, resp.text
    assert "register" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_draft_opportunity_rejected_400(volunteer_client, db_session):
    from services.volunteer_service.models import OpportunityStatus

    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid))
    opp = _make_opportunity(status=OpportunityStatus.DRAFT)
    db_session.add(opp)
    await db_session.commit()
    with _as_member(str(mid)):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert resp.status_code == 400, resp.text
    assert "not accepting" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_full_opportunity_rejected_400(volunteer_client, db_session):
    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid))
    opp = _make_opportunity(slots_needed=1, slots_filled=1)
    db_session.add(opp)
    await db_session.commit()
    with _as_member(str(mid)):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert resp.status_code == 400, resp.text
    assert "filled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_tier_too_low_403(volunteer_client, db_session):
    """TIER_1 member cannot claim a TIER_3 opportunity."""
    from services.volunteer_service.models import VolunteerTier

    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid, tier=VolunteerTier.TIER_1))
    opp = _make_opportunity(min_tier=VolunteerTier.TIER_3)
    db_session.add(opp)
    await db_session.commit()
    with _as_member(str(mid)):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_happy_path_auto_approves(volunteer_client, db_session):
    """OPEN_CLAIM → slot is APPROVED immediately and slots_filled bumps."""
    from sqlalchemy import select

    from services.volunteer_service.models import (
        SlotStatus,
        VolunteerOpportunity,
    )

    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid))
    opp = _make_opportunity(slots_needed=2, slots_filled=0)
    db_session.add(opp)
    await db_session.commit()
    opp_id = opp.id

    with _as_member(str(mid)):
        resp = await volunteer_client.post(
            f"/volunteers/opportunities/{opp_id}/claim"
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == SlotStatus.APPROVED.value

    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one()
    assert refreshed.slots_filled == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claim_twice_conflicts_409(volunteer_client, db_session):
    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid))
    opp = _make_opportunity(slots_needed=5)
    db_session.add(opp)
    await db_session.commit()

    with _as_member(str(mid)):
        first = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
        assert first.status_code == 201, first.text
        second = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_claim_then_404_when_none(volunteer_client, db_session):
    mid = await _seed_member(db_session)
    db_session.add(_make_profile(mid))
    opp = _make_opportunity(slots_needed=2)
    db_session.add(opp)
    await db_session.commit()

    with _as_member(str(mid)):
        add = await volunteer_client.post(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
        assert add.status_code == 201, add.text
        cancel = await volunteer_client.delete(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
        assert cancel.status_code == 204, cancel.text
        # Second cancel: nothing active left.
        again = await volunteer_client.delete(
            f"/volunteers/opportunities/{opp.id}/claim"
        )
    assert again.status_code == 404, again.text


# ---------------------------------------------------------------------------
# reward redemption guards (perks have monetary value)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_unknown_reward_404(volunteer_client):
    with _as_member(str(uuid.uuid4())):
        resp = await volunteer_client.post(
            f"/volunteers/rewards/{uuid.uuid4()}/redeem"
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_happy_then_double_redeem_409(volunteer_client, db_session):
    mid = await _seed_member(db_session)
    reward = _make_reward(mid)
    db_session.add(reward)
    await db_session.commit()

    with _as_member(str(mid)):
        first = await volunteer_client.post(
            f"/volunteers/rewards/{reward.id}/redeem"
        )
        assert first.status_code == 200, first.text
        assert first.json()["is_redeemed"] is True
        second = await volunteer_client.post(
            f"/volunteers/rewards/{reward.id}/redeem"
        )
    # A perk must never redeem twice.
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_expired_reward_422(volunteer_client, db_session):
    mid = await _seed_member(db_session)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    reward = _make_reward(mid, expires_at=past)
    db_session.add(reward)
    await db_session.commit()

    with _as_member(str(mid)):
        resp = await volunteer_client.post(
            f"/volunteers/rewards/{reward.id}/redeem"
        )
    assert resp.status_code == 422, resp.text
    assert "expired" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# register_as_volunteer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_register_then_duplicate_409(volunteer_client, db_session):
    mid = str(await _seed_member(db_session))
    with _as_member(mid):
        first = await volunteer_client.post(
            "/volunteers/profile/me",
            json={"preferred_roles": ["lane_marshal"], "notes": "weekends"},
        )
        assert first.status_code == 201, first.text
        dup = await volunteer_client.post(
            "/volunteers/profile/me",
            json={"preferred_roles": ["lane_marshal"]},
        )
    assert dup.status_code == 409, dup.text
