"""Members keeps plan capacity pinned while an external payment can arrive."""

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubEnrollment,
    ClubEnrollmentReservation,
    ClubPlanSession,
    ClubPlanVersion,
)
from services.members_service.routers import clubs, club_plan_admin
from services.members_service.schemas.club import ClubApplicationReservationRequest
from services.members_service.services import club_reservations

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_member_hold_protection_expiry_release_and_historical_preview(
    db_session, seed_member_row, monkeypatch
):
    now = utc_now()
    member = await seed_member_row(auth_id=f"holds-{uuid4()}")
    club = Club(name="Selected Club", slug=f"holds-{uuid4()}")
    db_session.add(club)
    await db_session.flush()
    plan = ClubPlanVersion(
        club_id=club.id,
        name="Selected quarter",
        capacity=1,
        club_fee_kobo=520000,
        period_start=now.date(),
        period_end=(now + timedelta(days=90)).date(),
        effective_from=now.date(),
        published_at=now,
        session_links=[
            ClubPlanSession(
                session_id=uuid4(),
                pool_id=uuid4(),
                title="Included",
                starts_at=now + timedelta(days=5),
                ends_at=now + timedelta(days=5, hours=1),
                fee_kobo=520000,
            )
        ],
    )
    db_session.add(plan)
    await db_session.flush()
    application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="approved",
        approved_payment_modes=["quarterly_prepaid"],
        community_experience_selected=False,
    )
    db_session.add(application)
    await db_session.commit()
    post = AsyncMock(return_value=httpx.Response(200, json={"status": "protected"}))
    monkeypatch.setattr(club_reservations, "internal_post", post)
    body = ClubApplicationReservationRequest(payment_reference=f"quarter-{uuid4()}")
    service = AuthUser(user_id="service")
    result = await clubs.reserve_club_application_capacity(
        application.id, body, service, db_session
    )
    assert result.session_holds
    reserved = post.await_args.kwargs["json"]
    assert reserved["plans"][0]["session_ids"] == [
        str(plan.session_links[0].session_id)
    ]
    assert reserved["member_id"] == str(member.id)
    await clubs.protect_club_application_capacity(
        application.id, body, service, db_session
    )
    row = await db_session.scalar(
        select(ClubEnrollmentReservation).where(
            ClubEnrollmentReservation.application_id == application.id
        )
    )
    assert row.status == "protected"
    row.expires_at = now - timedelta(hours=1)
    await db_session.commit()
    outsider = ClubApplication(
        id=uuid4(), member_id=member.id, club_id=club.id, plan_version_id=plan.id
    )
    with pytest.raises(HTTPException, match="capacity"):
        await clubs._assert_plan_capacity(
            db_session, application=outsider, plans=[plan], at=now
        )
    with pytest.raises(HTTPException, match="reconcile"):
        await clubs.release_club_application_capacity(
            application.id, body, service, db_session
        )
    assert row.status == "protected"
    closed = body.model_copy(
        update={
            "closure_evidence": "Provider support verified closed checkout CASE-1234"
        }
    )
    await clubs.release_club_application_capacity(
        application.id, closed, service, db_session
    )
    await clubs.release_club_application_capacity(
        application.id, closed, service, db_session
    )
    assert row.status == "released"
    enrollment = ClubEnrollment(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        application_id=application.id,
        payment_reference="historical",
        starts_at=now,
        ends_at=now + timedelta(days=90),
        status="active",
        payment_mode="quarterly_prepaid",
    )
    db_session.add(enrollment)
    await db_session.commit()
    post.reset_mock()
    preview = await club_plan_admin.sync_prepaid_reservations(
        plan.id, dry_run=True, db=db_session
    )
    assert preview["candidate_enrollment_ids"] == [str(enrollment.id)]
    assert not preview["synced_enrollment_ids"]
    post.assert_not_awaited()
