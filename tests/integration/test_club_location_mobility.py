import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.session_access import evaluate_session_access
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubEnrollment,
    ClubEnrollmentTransfer,
    ClubPlanVersion,
    ClubReadinessAssessment,
)
from services.members_service.routers.internal._schemas import ClubAccessCheck
from services.members_service.services.club_access import resolve_club_access_checks


def _club_plan(club_id, name, *, published=True):
    today = date.today()
    return ClubPlanVersion(
        club_id=club_id,
        name=name,
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_500_000,
        sessions_included=13,
        period_start=today - timedelta(days=30),
        period_end=today + timedelta(days=60),
        minimum_entry_sessions=5,
        effective_from=today - timedelta(days=60),
        effective_to=today + timedelta(days=60),
        published_at=utc_now() if published else None,
        is_active=True,
        capacity=None,
    )


@pytest.mark.asyncio
async def test_cross_location_visit_requires_explicit_host_opt_in(
    db_session, seed_member_row
):
    member = await seed_member_row(auth_id=f"visitor-{uuid.uuid4()}")
    home = Club(name="Home Club", slug=f"home-{uuid.uuid4().hex[:8]}")
    host = Club(name="Host Club", slug=f"host-{uuid.uuid4().hex[:8]}")
    db_session.add_all([home, host])
    await db_session.flush()

    home_plan = _club_plan(home.id, "Home quarter")
    application = ClubApplication(
        member_id=member.id,
        club_id=home.id,
        plan_version_id=home_plan.id,
        status="enrolled",
        community_experience_selected=False,
        approved_payment_modes=["transition_per_session"],
        transition_expires_at=date.today() + timedelta(days=60),
        selected_payment_mode="transition_per_session",
    )
    db_session.add_all([home_plan, application])
    await db_session.flush()
    db_session.add(
        ClubEnrollment(
            member_id=member.id,
            club_id=home.id,
            plan_version_id=home_plan.id,
            application_id=application.id,
            payment_reference="TRANSITION-HOME",
            starts_at=utc_now() - timedelta(days=1),
            ends_at=utc_now() + timedelta(days=30),
            payment_mode="transition_per_session",
            status="active",
        )
    )
    await db_session.commit()

    when = utc_now() + timedelta(days=3)
    closed = await resolve_club_access_checks(
        db_session,
        [
            ClubAccessCheck(
                context_key="closed",
                session_id=uuid.uuid4(),
                member_id=member.id,
                club_id=host.id,
                club_access_mode="plan_included",
                at=when,
                allows_visiting_club_members=False,
            )
        ],
    )
    assert closed[0]["allowed"] is False

    open_visit = await resolve_club_access_checks(
        db_session,
        [
            ClubAccessCheck(
                context_key="open",
                session_id=uuid.uuid4(),
                member_id=member.id,
                club_id=host.id,
                club_access_mode="plan_included",
                at=when,
                allows_visiting_club_members=True,
            )
        ],
    )
    assert open_visit[0]["allowed"] is True
    assert open_visit[0]["source"] == "club_visit"
    assert open_visit[0]["club_id"] == home.id
    assert open_visit[0]["payment_mode"] == "transition_per_session"


@pytest.mark.parametrize(
    "access_mode,visitor_fee,expected_fee,expected_code",
    [
        ("plan_included", 850_000, 850_000, "club_visit"),
        ("active_club", None, 1_200_000, "club_visit"),
        ("paid_addon", 850_000, 1_200_000, "club_visit_paid_addon"),
    ],
)
def test_cross_location_visit_uses_host_pricing(
    access_mode, visitor_fee, expected_fee, expected_code
):
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        session_type="club",
        status="scheduled",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=2),
        club_access_mode=access_mode,
        pool_fee=1_200_000,
        visiting_club_fee_kobo=visitor_fee,
        pod_id=None,
    )
    decision = evaluate_session_access(
        {"member_id": str(uuid.uuid4())},
        session,
        now=now,
        club_access_result={"allowed": True, "source": "club_visit"},
    )
    assert decision.allowed if hasattr(decision, "allowed") else decision.bookable
    assert decision.bookable is True
    assert decision.access_source == "club_visit"
    assert decision.fee_amount_kobo == expected_fee
    assert decision.rate_code == expected_code


async def _seed_transfer(db_session, seed_member_row, *, payment_mode):
    member = await seed_member_row(auth_id=f"move-{uuid.uuid4()}")
    source_club = Club(
        name="Source Club",
        slug=f"source-{uuid.uuid4().hex[:8]}",
    )
    target_club = Club(
        name="Target Club",
        slug=f"target-{uuid.uuid4().hex[:8]}",
    )
    db_session.add_all([source_club, target_club])
    await db_session.flush()

    source_plan = _club_plan(source_club.id, "Source quarter")
    target_plan = _club_plan(target_club.id, "Target quarter")
    db_session.add_all([source_plan, target_plan])
    await db_session.flush()

    transition_expiry = (
        date.today() + timedelta(days=45)
        if payment_mode == "transition_per_session"
        else None
    )
    source_application = ClubApplication(
        member_id=member.id,
        club_id=source_club.id,
        plan_version_id=source_plan.id,
        status="enrolled",
        community_experience_selected=False,
        approved_payment_modes=[payment_mode],
        transition_expires_at=transition_expiry,
        selected_payment_mode=payment_mode,
    )
    db_session.add(source_application)
    await db_session.flush()
    db_session.add(
        ClubReadinessAssessment(
            application_id=source_application.id,
            self_report={"can_swim_25m_continuously": True},
            observed_checks={"continuous_25m": True},
            outcome="club_ready",
            completed_at=utc_now() - timedelta(days=10),
        )
    )
    source_enrollment = ClubEnrollment(
        member_id=member.id,
        club_id=source_club.id,
        plan_version_id=source_plan.id,
        application_id=source_application.id,
        payment_reference="SOURCE-PAYMENT",
        starts_at=utc_now() - timedelta(days=5),
        ends_at=utc_now() + timedelta(days=45),
        payment_mode=payment_mode,
        status="active",
    )
    db_session.add(source_enrollment)
    await db_session.commit()
    return member, source_club, target_club, target_plan, source_enrollment


@pytest.mark.asyncio
async def test_transition_member_can_change_home_club_without_second_quarter_charge(
    members_client, db_session, seed_member_row
):
    member, source_club, target_club, target_plan, source = await _seed_transfer(
        db_session, seed_member_row, payment_mode="transition_per_session"
    )

    preview = await members_client.post(
        f"/clubs/admin/enrollments/{source.id}/location-transfer/preview",
        json={"target_plan_version_id": str(target_plan.id)},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["can_execute_now"] is True
    assert body["requires_financial_reconciliation"] is False
    assert body["source_club_id"] == str(source_club.id)
    assert body["target_club_id"] == str(target_club.id)

    moved = await members_client.post(
        f"/clubs/admin/enrollments/{source.id}/location-transfer",
        json={"target_plan_version_id": str(target_plan.id), "note": "Permanent move"},
    )
    assert moved.status_code == 200, moved.text
    moved_body = moved.json()

    await db_session.refresh(source)
    assert source.status == "transferred"

    target = await db_session.get(
        ClubEnrollment, uuid.UUID(moved_body["target_enrollment_id"])
    )
    assert target is not None
    assert target.club_id == target_club.id
    assert target.member_id == member.id
    assert target.payment_mode == "transition_per_session"
    assert target.ends_at > target.starts_at
    assert target.payment_reference.startswith("club-transfer:")

    audit = await db_session.get(
        ClubEnrollmentTransfer, uuid.UUID(moved_body["transfer_id"])
    )
    assert audit is not None
    assert audit.source_enrollment_id == source.id
    assert audit.target_enrollment_id == target.id
    assert audit.source_club_id == source_club.id
    assert audit.target_club_id == target_club.id


@pytest.mark.asyncio
async def test_prepaid_location_move_requires_financial_reconciliation_and_is_non_mutating(
    members_client, db_session, seed_member_row
):
    _, _, _, target_plan, source = await _seed_transfer(
        db_session, seed_member_row, payment_mode="quarterly_prepaid"
    )
    original_end = source.ends_at

    preview = await members_client.post(
        f"/clubs/admin/enrollments/{source.id}/location-transfer/preview",
        json={"target_plan_version_id": str(target_plan.id)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_execute_now"] is False
    assert preview.json()["requires_financial_reconciliation"] is True

    moved = await members_client.post(
        f"/clubs/admin/enrollments/{source.id}/location-transfer",
        json={"target_plan_version_id": str(target_plan.id)},
    )
    assert moved.status_code == 409

    await db_session.refresh(source)
    assert source.status == "active"
    assert source.ends_at == original_end
    audits = list(
        (
            await db_session.execute(
                select(ClubEnrollmentTransfer).where(
                    ClubEnrollmentTransfer.source_enrollment_id == source.id
                )
            )
        ).scalars()
    )
    assert audits == []
