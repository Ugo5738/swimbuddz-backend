"""Regression coverage for transition members missing from Club attendance."""

import uuid
from datetime import timedelta

import pytest

from libs.auth.dependencies import get_current_user, require_admin
from libs.common.datetime_utils import utc_now
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubEnrollment,
    ClubPlanVersion,
    MemberMembership,
    Pod,
    PodAssignment,
)
from services.members_service.schemas.club_roster import ClubSessionRosterContext
from services.members_service.services.club_roster import club_session_roster
from tests.conftest import make_member_user
from tests.factories import MemberFactory


async def enroll(db, member, club, *, starts, ends, mode="transition_per_session"):
    plan = ClubPlanVersion(
        club_id=club.id,
        name="Test quarter",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_760_000,
        sessions_included=13,
        period_start=starts.date(),
        period_end=ends.date(),
        effective_from=starts.date(),
        effective_to=ends.date(),
        pool_id=club.default_pool_id,
        published_at=starts,
        is_active=True,
    )
    db.add(plan)
    await db.flush()
    application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="enrolled",
        community_experience_selected=False,
        approved_payment_modes=[mode],
        selected_payment_mode=mode,
    )
    db.add(application)
    await db.flush()
    enrollment = ClubEnrollment(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        application_id=application.id,
        payment_reference=f"ROSTER-{uuid.uuid4()}",
        starts_at=starts,
        ends_at=ends,
        payment_mode=mode,
        status="active",
        pool_id=club.default_pool_id,
    )
    db.add(enrollment)
    await db.flush()
    return enrollment


@pytest.mark.asyncio
async def test_roster_exposes_only_applicable_dated_commercial_fee(
    members_client, db_session
):
    from services.members_service.models import ClubCommercialAdjustment

    now = utc_now()
    member = MemberFactory.create(first_name="AY")
    club = Club(name="VI", slug=f"vi-{uuid.uuid4()}", default_pool_id=uuid.uuid4())
    db_session.add_all([member, club])
    await db_session.flush()
    enrollment = await enroll(
        db_session,
        member,
        club,
        starts=now - timedelta(days=3),
        ends=now + timedelta(days=30),
    )
    db_session.add(
        ClubCommercialAdjustment(
            id=uuid.uuid4(),
            enrollment_id=enrollment.id,
            actor_auth_id="admin",
            reason="Agreed regular practice fee for this member",
            original_terms={"standard_session_fee_kobo": 1_500_000},
            approved_terms={},
            session_fee_kobo=1_000_000,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=10),
        )
    )
    await db_session.flush()
    context = ClubSessionRosterContext(
        session_id=uuid.uuid4(), at=now, club_id=club.id, pool_id=club.default_pool_id
    )
    rows = await club_session_roster(db_session, context)
    assert (
        next(row for row in rows if row.member_id == member.id).fee_amount_kobo
        == 1_000_000
    )
    expired = await club_session_roster(
        db_session, context.model_copy(update={"at": now + timedelta(days=11)})
    )
    assert (
        next(row for row in expired if row.member_id == member.id).fee_amount_kobo
        is None
    )
    addon = await club_session_roster(
        db_session, context.model_copy(update={"club_access_mode": "paid_addon"})
    )
    assert (
        next(row for row in addon if row.member_id == member.id).fee_amount_kobo is None
    )


@pytest.mark.asyncio
async def test_roster_includes_transition_and_legacy_but_not_academy_only(
    members_client, db_session
):
    now = utc_now()
    transition = MemberFactory.create(first_name="Transition")
    legacy = MemberFactory.create(first_name="Legacy")
    academy = MemberFactory.create(first_name="Academy")
    stale = MemberFactory.create(first_name="Stale")
    club = Club(name="Yaba", slug=f"yaba-{uuid.uuid4()}", default_pool_id=uuid.uuid4())
    db_session.add_all([transition, legacy, academy, stale, club])
    await db_session.flush()
    for member, tiers, club_until, academy_until in [
        (transition, ["community"], now - timedelta(days=30), None),
        (legacy, ["club"], now + timedelta(days=30), None),
        (academy, ["academy"], None, now + timedelta(days=30)),
        (stale, ["club"], now - timedelta(days=30), None),
    ]:
        db_session.add(
            MemberMembership(
                member_id=member.id,
                primary_tier=tiers[0],
                active_tiers=tiers,
                club_paid_until=club_until,
                academy_paid_until=academy_until,
            )
        )
    await enroll(
        db_session,
        transition,
        club,
        starts=now - timedelta(days=10),
        ends=now + timedelta(days=40),
    )
    await db_session.commit()
    session_id = uuid.uuid4()
    response = await members_client.get(
        "/members/club-session-roster",
        params={
            "session_id": str(session_id),
            "at": now.isoformat(),
            "club_id": str(club.id),
            "pool_id": str(club.default_pool_id),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == str(session_id)
    rows = {row["member_id"]: row for row in body["members"]}
    assert set(rows) == {str(transition.id), str(legacy.id)}
    assert rows[str(transition.id)]["access_source"] == "club_transition"


@pytest.mark.asyncio
async def test_general_roster_includes_other_club_without_making_it_prepaid(db_session):
    now = utc_now()
    member = MemberFactory.create()
    home = Club(
        name="Festac", slug=f"festac-{uuid.uuid4()}", default_pool_id=uuid.uuid4()
    )
    host = Club(name="Yaba", slug=f"yaba-{uuid.uuid4()}", default_pool_id=uuid.uuid4())
    db_session.add_all([member, home, host])
    await db_session.flush()
    await enroll(
        db_session,
        member,
        home,
        starts=now - timedelta(days=10),
        ends=now + timedelta(days=40),
        mode="quarterly_prepaid",
    )
    await db_session.commit()
    rows = await club_session_roster(
        db_session,
        ClubSessionRosterContext(
            session_id=uuid.uuid4(),
            at=now,
            club_id=host.id,
            pool_id=host.default_pool_id,
        ),
    )
    assert [row.member_id for row in rows] == [member.id]
    assert rows[0].access_source == "club_visit"


@pytest.mark.asyncio
async def test_historical_pod_roster_uses_assignment_at_session_time(db_session):
    now = utc_now()
    member = MemberFactory.create()
    other = MemberFactory.create()
    club = Club(name="Yaba", slug=f"yaba-{uuid.uuid4()}")
    db_session.add_all([member, other, club])
    await db_session.flush()
    pod = Pod(
        club_id=club.id,
        name="Dolphins",
        slug=f"dolphins-{uuid.uuid4()}",
        pod_lead_id=member.id,
        created_by=member.id,
        review_due_at=now + timedelta(days=30),
        default_session_day="sat",
        default_session_time=now.time().replace(tzinfo=None),
    )
    db_session.add(pod)
    await db_session.flush()
    for person in [member, other]:
        db_session.add(
            MemberMembership(
                member_id=person.id,
                primary_tier="club",
                club_paid_until=now + timedelta(days=10),
            )
        )
    db_session.add_all(
        [
            PodAssignment(
                pod_id=pod.id,
                member_id=member.id,
                joined_at=now - timedelta(days=20),
                left_at=now - timedelta(days=2),
                assigned_by="admin",
            ),
            PodAssignment(
                pod_id=pod.id,
                member_id=other.id,
                joined_at=now - timedelta(days=1),
                assigned_by="admin",
            ),
        ]
    )
    await db_session.commit()
    context = ClubSessionRosterContext(
        session_id=uuid.uuid4(),
        at=now - timedelta(days=5),
        club_id=club.id,
        pod_id=pod.id,
    )
    assert [
        row.member_id for row in await club_session_roster(db_session, context)
    ] == [member.id]
    assert [
        row.member_id
        for row in await club_session_roster(
            db_session, context.model_copy(update={"at": now})
        )
    ] == [other.id]


@pytest.mark.asyncio
async def test_roster_requires_admin(members_client):
    from services.members_service.app.main import app

    app.dependency_overrides.pop(require_admin)
    app.dependency_overrides[get_current_user] = lambda: make_member_user()
    response = await members_client.get(
        "/members/club-session-roster",
        params={
            "session_id": str(uuid.uuid4()),
            "at": utc_now().isoformat(),
        },
    )
    assert response.status_code == 403
