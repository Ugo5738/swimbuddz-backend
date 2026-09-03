import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from services.members_service.models import (
    Club,
    ClubApplication,
    ClubEnrollment,
    ClubPlanVersion,
    ClubReadinessAssessment,
)
from libs.auth.models import AuthUser
from services.members_service.routers.clubs import create_club_application
from services.members_service.schemas.club import ClubApplicationCreate


@pytest.mark.asyncio
async def test_transition_quote_and_activation_are_zero_quarterly_and_snapshotted(
    members_client, db_session, seed_member_row
):
    member = await seed_member_row(auth_id=f"transition-{uuid.uuid4()}")
    pool_id = uuid.uuid4()
    area_id = uuid.uuid4()
    club = Club(
        name="Yaba Club",
        slug=f"yaba-transition-{uuid.uuid4().hex[:8]}",
        default_pool_id=pool_id,
        operating_area_id=area_id,
    )
    db_session.add(club)
    await db_session.flush()
    plan = ClubPlanVersion(
        club_id=club.id,
        pool_id=pool_id,
        operating_area_id=area_id,
        name="Yaba Q4 2026",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_500_000,
        community_experience_fee_kobo=3_000_000,
        community_experience_default_selected=False,
        sessions_included=13,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 12, 31),
        minimum_entry_sessions=5,
        refreshments_included=True,
        capacity=20,
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()
    application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="approved",
        community_experience_selected=False,
        approved_payment_modes=["transition_per_session"],
        transition_session_rate_kobo=520_000,
        transition_expires_at=date(2026, 12, 31),
    )
    db_session.add(application)
    await db_session.commit()

    quote_response = await members_client.get(
        f"/clubs/internal/applications/{application.id}/payment-context"
    )

    assert quote_response.status_code == 200, quote_response.text
    quote = quote_response.json()
    assert quote["payment_mode"] == "transition_per_session"
    assert quote["club_fee_kobo"] == 0
    assert quote["club_items"][0]["amount_kobo"] == 0
    assert quote["annual_membership_fee_kobo"] == 2_000_000
    assert quote["subtotal_kobo"] == 2_000_000
    assert quote["transition_session_rate_kobo"] == 520_000
    assert quote["transition_expires_at"] == "2026-12-31"

    activation_response = await members_client.post(
        f"/clubs/internal/applications/{application.id}/activate",
        json={
            "payment_reference": "PAY-TRANSITION-YABA",
            "payment_mode": "transition_per_session",
            "starts_at": "2026-09-03T12:00:00Z",
        },
    )

    assert activation_response.status_code == 200, activation_response.text
    enrollment = (
        await db_session.execute(
            select(ClubEnrollment).where(
                ClubEnrollment.application_id == application.id
            )
        )
    ).scalar_one()
    assert enrollment.starts_at == datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert enrollment.ends_at == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert enrollment.pool_id == pool_id
    assert enrollment.operating_area_id == area_id
    assert enrollment.payment_mode == "transition_per_session"
    assert enrollment.transition_session_rate_kobo == 520_000


@pytest.mark.asyncio
async def test_active_membership_covering_transition_is_not_charged_again(
    members_client, db_session, seed_member_row
):
    from services.members_service.models import MemberMembership

    member = await seed_member_row(auth_id=f"covered-transition-{uuid.uuid4()}")
    club = Club(name="Covered Club", slug=f"covered-{uuid.uuid4().hex[:8]}")
    db_session.add(club)
    await db_session.flush()
    plan = ClubPlanVersion(
        club_id=club.id,
        name="Covered Q4 2026",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_500_000,
        sessions_included=13,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 12, 31),
        minimum_entry_sessions=5,
        effective_from=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()
    application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="approved",
        community_experience_selected=False,
        approved_payment_modes=["transition_per_session"],
        transition_session_rate_kobo=500_000,
        transition_expires_at=date(2026, 12, 31),
    )
    db_session.add_all(
        [
            application,
            MemberMembership(
                member_id=member.id,
                primary_tier="community",
                active_tiers=["community"],
                community_paid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    response = await members_client.get(
        f"/clubs/internal/applications/{application.id}/payment-context",
        params={"payment_mode": "transition_per_session"},
    )

    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["club_fee_kobo"] == 0
    assert quote["annual_membership_fee_kobo"] == 0
    assert quote["subtotal_kobo"] == 0


@pytest.mark.asyncio
async def test_new_club_period_reuses_completed_readiness_without_auto_enrollment(
    db_session, seed_member_row
):
    today = date.today()
    member = await seed_member_row(auth_id=f"renewal-{uuid.uuid4()}")
    club = Club(name="Renewal Club", slug=f"renewal-{uuid.uuid4().hex[:8]}")
    db_session.add(club)
    await db_session.flush()
    old_plan = ClubPlanVersion(
        club_id=club.id,
        name="Previous Club period",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_000_000,
        sessions_included=13,
        period_start=today - timedelta(days=120),
        period_end=today - timedelta(days=30),
        minimum_entry_sessions=5,
        effective_from=today - timedelta(days=180),
        is_active=True,
    )
    new_plan = ClubPlanVersion(
        club_id=club.id,
        name="New Club period",
        billing_cycle="quarterly",
        currency="NGN",
        club_fee_kobo=6_500_000,
        sessions_included=13,
        period_start=today,
        period_end=today + timedelta(days=90),
        minimum_entry_sessions=5,
        effective_from=today - timedelta(days=1),
        is_active=True,
    )
    db_session.add_all([old_plan, new_plan])
    await db_session.flush()
    previous_application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=old_plan.id,
        status="enrolled",
        community_experience_selected=False,
        approved_payment_modes=["transition_per_session"],
        transition_session_rate_kobo=500_000,
        transition_expires_at=today - timedelta(days=1),
        selected_payment_mode="transition_per_session",
    )
    db_session.add(previous_application)
    await db_session.flush()
    db_session.add(
        ClubReadinessAssessment(
            application_id=previous_application.id,
            self_report={"can_swim_25m_continuously": True},
            observed_checks={"continuous_25m": True},
            outcome="club_ready",
            nonstop_distance_m=50,
            completed_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
    )
    await db_session.commit()

    created = await create_club_application(
        ClubApplicationCreate(
            plan_version_id=new_plan.id,
            community_experience_selected=False,
        ),
        current_user=AuthUser(
            user_id=member.auth_id,
            email=member.email,
            role="authenticated",
            app_metadata={"roles": ["member"]},
            user_metadata={},
        ),
        db=db_session,
    )

    assert created.status == "approved"
    assert created.approved_payment_modes == ["quarterly_prepaid"]
    assert created.selected_payment_mode is None
    assert created.assessment is not None
    assert created.assessment.self_report[
        "readiness_reused_from_application_id"
    ] == str(previous_application.id)
    assert (
        await db_session.execute(
            select(ClubEnrollment).where(ClubEnrollment.application_id == created.id)
        )
    ).scalar_one_or_none() is None
