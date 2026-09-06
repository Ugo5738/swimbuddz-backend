"""Integration tests for members_service PUBLIC API endpoints.

These test the user-facing endpoints (GET /members/me, PATCH /members/me,
GET /members/, POST /members/, etc.) that were previously untested.
All existing tests only covered /internal/* endpoints.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from tests.conftest import (
    make_member_user,
    override_auth,
)
from tests.factories import MemberFactory

# ---------------------------------------------------------------------------
# GET /members/me — Current member profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_current_member_profile(members_client, db_session):
    """Authenticated member can fetch their own profile."""
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    db_session.add(member)
    await db_session.commit()

    from services.members_service.app.main import app

    with override_auth(app, user):
        with patch(
            "services.members_service.routers.members.resolve_member_media_urls",
            new_callable=AsyncMock,
        ) as mock_media:
            mock_media.side_effect = lambda d: d  # pass-through
            response = await members_client.get("/members/me")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["email"] == member.email
    assert data["first_name"] == member.first_name


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_current_member_profile_not_found(members_client, db_session):
    """Returns 404 when authenticated user has no member profile."""
    user = make_member_user(user_id="no-profile-user")

    from services.members_service.app.main import app

    with override_auth(app, user):
        response = await members_client.get("/members/me")

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_membership_history_guides_expired_legacy_club_member(
    members_client, db_session
):
    """Former Club members see the recorded end date and a renewal action."""
    from services.members_service.app.main import app
    from services.members_service.models import MemberMembership

    now = datetime.now(timezone.utc)
    user = make_member_user()
    member = MemberFactory.create(
        auth_id=user.user_id,
        email=user.email,
        created_at=now - timedelta(days=400),
    )
    membership = MemberMembership(
        member_id=member.id,
        primary_tier="community",
        active_tiers=["community"],
        declared_tiers=["community", "club"],
        community_paid_until=now + timedelta(days=100),
        club_paid_until=now - timedelta(days=10),
        club_billing_cycle_months=3,
    )
    db_session.add_all([member, membership])
    await db_session.commit()

    with override_auth(app, user):
        response = await members_client.get("/members/me/membership-history")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["club_renewal_status"] == "due"
    assert data["club_action"] == "renew"
    club_period = next(
        period for period in data["periods"] if period["product"] == "club"
    )
    assert club_period["status"] == "expired"
    assert club_period["dates_are_estimated"] is True
    assert club_period["ends_at"].startswith(
        (now - timedelta(days=10)).date().isoformat()
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_membership_history_uses_exact_club_enrollment_dates(
    members_client, db_session
):
    """New Club enrollment snapshots are exposed as exact dated history."""
    from services.members_service.app.main import app
    from services.members_service.models import (
        Club,
        ClubApplication,
        ClubEnrollment,
        ClubPlanVersion,
        MemberMembership,
    )

    now = datetime.now(timezone.utc)
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    membership = MemberMembership(
        member_id=member.id,
        primary_tier="club",
        active_tiers=["club", "community"],
        declared_tiers=["community", "club"],
        community_paid_until=now + timedelta(days=365),
        # Simulate a legacy projection that describes the same entitlement as
        # the exact ClubEnrollment below.
        club_paid_until=now + timedelta(days=30),
        club_billing_cycle_months=3,
    )
    club = Club(name="Yaba Club", slug=f"history-{uuid.uuid4().hex}")
    db_session.add_all([member, membership, club])
    await db_session.flush()
    plan = ClubPlanVersion(
        club_id=club.id,
        name="2026 Q4 Club",
        club_fee_kobo=4_250_000,
        period_start=date.today(),
        period_end=date.today() + timedelta(days=89),
        effective_from=date.today() - timedelta(days=1),
    )
    db_session.add(plan)
    await db_session.flush()
    application = ClubApplication(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        status="activated",
    )
    db_session.add(application)
    await db_session.flush()
    enrollment = ClubEnrollment(
        member_id=member.id,
        club_id=club.id,
        plan_version_id=plan.id,
        application_id=application.id,
        payment_reference=f"HISTORY-{uuid.uuid4().hex}",
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=30),
        payment_mode="transition_per_session",
    )
    db_session.add(enrollment)
    await db_session.commit()

    with override_auth(app, user):
        response = await members_client.get("/members/me/membership-history")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["club_renewal_status"] == "active"
    exact_period = next(
        period for period in data["periods"] if period["id"] == str(enrollment.id)
    )
    assert exact_period["label"] == "Yaba Club"
    assert exact_period["payment_mode"] == "transition_per_session"
    assert exact_period["dates_are_estimated"] is False
    assert [
        period for period in data["periods"] if period["product"] == "club"
    ] == [exact_period]


# ---------------------------------------------------------------------------
# GET /members/ — List members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_members(members_client, db_session):
    """Admin can list all members."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    db_session.add_all([m1, m2])
    await db_session.commit()

    with patch(
        "services.members_service.routers.members.resolve_media_urls",
        new_callable=AsyncMock,
        return_value={},
    ):
        response = await members_client.get("/members/")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_members_with_pagination(members_client, db_session):
    """Pagination parameters are respected."""
    for _ in range(5):
        db_session.add(MemberFactory.create())
    await db_session.commit()

    with patch(
        "services.members_service.routers.members.resolve_media_urls",
        new_callable=AsyncMock,
        return_value={},
    ):
        response = await members_client.get("/members/?skip=0&limit=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_members_searches_name_and_email(members_client, db_session):
    """Vault access search can find members beyond a preloaded first page."""
    target = MemberFactory.create(
        first_name="VaultSearch",
        last_name="Curator",
        email="vault-curator@example.com",
    )
    db_session.add_all([target, MemberFactory.create()])
    await db_session.commit()

    with patch(
        "services.members_service.routers.members.resolve_media_urls",
        new_callable=AsyncMock,
        return_value={},
    ):
        response = await members_client.get("/members/?search=VaultSearch&limit=20")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [str(target.id)]


# ---------------------------------------------------------------------------
# GET /members/stats — Member statistics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_stats(members_client, db_session):
    """Returns member statistics."""
    m = MemberFactory.create(approval_status="approved", registration_complete=True)
    db_session.add(m)
    await db_session.commit()

    response = await members_client.get("/members/stats")

    assert response.status_code == 200
    data = response.json()
    assert "total_members" in data
    assert "active_members" in data
    assert "approved_members" in data
    assert "pending_approvals" in data
    assert data["total_members"] >= 1


# ---------------------------------------------------------------------------
# GET /members/public — Public member list (no auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_public_members(members_client, db_session):
    """Public endpoint returns limited member info."""
    m = MemberFactory.create()
    db_session.add(m)
    await db_session.commit()

    response = await members_client.get("/members/public")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# GET /members/{member_id} — Get member by ID (admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_id_admin(members_client, db_session):
    """Admin can get a specific member by ID."""
    m = MemberFactory.create()
    db_session.add(m)
    await db_session.commit()

    with patch(
        "services.members_service.routers.members.resolve_member_media_urls",
        new_callable=AsyncMock,
    ) as mock_media:
        mock_media.side_effect = lambda d: d
        response = await members_client.get(f"/members/{m.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(m.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_id_not_found(members_client, db_session):
    """Returns 404 for non-existent member."""
    fake_id = str(uuid.uuid4())

    with patch(
        "services.members_service.routers.members.resolve_member_media_urls",
        new_callable=AsyncMock,
    ) as mock_media:
        mock_media.side_effect = lambda d: d
        response = await members_client.get(f"/members/{fake_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /members/ — Create member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_member(members_client, db_session):
    """Create a new member directly."""
    payload = {
        "email": f"new-{uuid.uuid4().hex[:6]}@test.com",
        "first_name": "New",
        "last_name": "Member",
        "auth_id": str(uuid.uuid4()),
    }

    response = await members_client.post("/members/", json=payload)

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["email"] == payload["email"]
    assert data["first_name"] == "New"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_member_duplicate_email(members_client, db_session):
    """Returns 400 when email already registered."""
    existing = MemberFactory.create()
    db_session.add(existing)
    await db_session.commit()

    payload = {
        "email": existing.email,
        "first_name": "Dup",
        "last_name": "User",
        "auth_id": str(uuid.uuid4()),
    }

    response = await members_client.post("/members/", json=payload)

    assert response.status_code == 400
    assert "already registered" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /members/bulk-basic — Bulk member lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_basic_lookup(members_client, db_session):
    """Bulk lookup returns dict mapping ID to basic info."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    db_session.add_all([m1, m2])
    await db_session.commit()

    with patch(
        "services.members_service.routers.members.resolve_media_urls",
        new_callable=AsyncMock,
        return_value={},
    ):
        response = await members_client.post(
            "/members/bulk-basic",
            json=[str(m1.id), str(m2.id)],
        )

    assert response.status_code == 200
    data = response.json()
    assert str(m1.id) in data
    assert str(m2.id) in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_basic_empty_list(members_client, db_session):
    """Bulk lookup with empty list returns empty dict."""
    response = await members_client.post("/members/bulk-basic", json=[])

    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_basic_exceeds_limit(members_client, db_session):
    """Bulk lookup rejects > 50 IDs."""
    ids = [str(uuid.uuid4()) for _ in range(51)]

    response = await members_client.post("/members/bulk-basic", json=ids)

    assert response.status_code == 400
    assert "50" in response.json()["detail"]
