"""Integration tests for members_service Pod endpoints.

Covers the admin and member-facing pod APIs that landed when pods moved
from sessions_service to members_service in May 2026 — see
docs/club/POD_OPERATIONS.md.

External side effects (chat-channel ensure / membership reconcile) are
patched at the call site (``services.members_service.routers.pods.*``)
per the MEMORY.md "patch where it's called from" rule. The chat layer
itself is verified separately in test_chat_internal.py.

Auth note: the pod router calls ``_resolve_member_id(current_user, db)``
on admin endpoints that need an actor (``created_by`` / ``assigned_by_id``
on create / add-member / transfer). That requires a real ``Member`` row
whose ``auth_id`` matches the authenticated user's ``user_id``. Tests
use ``_setup_admin_with_member`` to satisfy that — the default
``members_client`` fixture wires a random admin without a Member, which
would 403."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from libs.auth.dependencies import (
    get_current_user,
    get_optional_user,
    require_admin,
    require_coach,
    require_safeguarding_admin,
    require_service_role,
)
from tests.conftest import make_admin_user, make_member_user, override_auth
from tests.factories import ClubFactory, MemberFactory, PodFactory


# ---------------------------------------------------------------------------
# Shared patches: chat-sync side effects.
# ---------------------------------------------------------------------------

_ENSURE_CHAT = "services.members_service.routers.pods.ensure_pod_channel"
_RECONCILE_CHAT = "services.members_service.routers.pods.reconcile_pod_membership"


def _silence_chat_sync():
    """Patch the two chat-sync calls used by the pod router so tests
    don't try to talk to chat_service. Returns a dict of patchers."""
    return {
        "ensure": patch(_ENSURE_CHAT, new_callable=AsyncMock, return_value=None),
        "reconcile": patch(
            _RECONCILE_CHAT, new_callable=AsyncMock, return_value=True
        ),
    }


async def _setup_admin_with_member(db_session):
    """Seed an admin Member into the test DB and override the
    members_service app's auth dependencies to return that user.

    Returns the admin Member so tests can assert against ``created_by``
    if they want to. Required for any admin endpoint that records the
    actor (create pod, add member, transfer).

    Generates a unique email per call — ``make_admin_user``'s default
    ``admin@admin.com`` collides on the email unique index when a prior
    test (or seed data) already committed it outside our transaction."""
    unique = uuid.uuid4().hex[:8]
    user = make_admin_user(
        user_id=str(uuid.uuid4()), email=f"admin-{unique}@test.com"
    )
    admin = MemberFactory.create(auth_id=user.user_id, email=user.email)
    db_session.add(admin)
    await db_session.commit()

    from services.members_service.app.main import app

    async def _get_user():
        return user

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[get_optional_user] = _get_user
    app.dependency_overrides[require_admin] = _get_user
    app.dependency_overrides[require_coach] = _get_user
    app.dependency_overrides[require_safeguarding_admin] = _get_user
    app.dependency_overrides[require_service_role] = _get_user
    return admin


# ---------------------------------------------------------------------------
# Admin: POST /admin/members/pods (create)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_pod_with_explicit_schedule(members_client, db_session):
    """Admin creates a pod with all fields specified — the response
    echoes them back and the chat channel ensure is fired exactly once."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    db_session.add_all([club, lead])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"] as mock_ensure, patches["reconcile"]:
        response = await members_client.post(
            "/admin/members/pods",
            json={
                "club_id": str(club.id),
                "name": "Dolphins Pod",
                "handle": "dolphins",
                "pod_lead_id": str(lead.id),
                "min_size": 2,
                "max_size": 5,
                "default_session_day": "sat",
                "default_session_time": "09:00",
                "default_session_duration_minutes": 180,
            },
        )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["name"] == "Dolphins Pod"
    assert data["handle"] == "dolphins"
    assert data["pod_lead_id"] == str(lead.id)
    assert data["club_id"] == str(club.id)
    assert data["status"] == "active"
    assert data["visibility"] == "public"
    assert data["active_member_count"] == 0
    # chat-sync was called once with the right args
    mock_ensure.assert_awaited_once()
    kwargs = mock_ensure.call_args.kwargs
    assert kwargs["pod_name"] == "dolphins"  # handle preferred over name
    assert kwargs["pod_lead_id"] == lead.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_pod_inherits_club_schedule_when_omitted(
    members_client, db_session
):
    """When schedule fields are omitted, the pod inherits from the parent
    Club — verifies the schedule-inheritance contract."""
    from datetime import time

    from services.members_service.models import DayOfWeek

    await _setup_admin_with_member(db_session)
    club = ClubFactory.create(
        default_session_day=DayOfWeek.WED,
        default_session_time=time(18, 30),
        default_session_duration_minutes=120,
    )
    lead = MemberFactory.create()
    db_session.add_all([club, lead])
    await db_session.commit()

    with patch(_ENSURE_CHAT, new_callable=AsyncMock):
        response = await members_client.post(
            "/admin/members/pods",
            json={
                "club_id": str(club.id),
                "pod_lead_id": str(lead.id),
                # No schedule fields → should inherit from club
            },
        )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["default_session_day"] == "wed"
    assert data["default_session_time"].startswith("18:30")
    assert data["default_session_duration_minutes"] == 120


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_pod_handle_collision_returns_409(
    members_client, db_session
):
    """Two pods in the same Club can't share a handle."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    existing = PodFactory.create(
        club_id=club.id, pod_lead_id=lead.id, handle="dolphins"
    )
    db_session.add_all([club, lead, existing])
    await db_session.commit()

    with patch(_ENSURE_CHAT, new_callable=AsyncMock):
        response = await members_client.post(
            "/admin/members/pods",
            json={
                "club_id": str(club.id),
                "pod_lead_id": str(lead.id),
                "handle": "dolphins",
            },
        )

    assert response.status_code == 409
    assert "handle" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_pod_with_invalid_size_range_returns_400(
    members_client, db_session
):
    """min_size must be <= max_size."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    db_session.add_all([club, lead])
    await db_session.commit()

    with patch(_ENSURE_CHAT, new_callable=AsyncMock):
        response = await members_client.post(
            "/admin/members/pods",
            json={
                "club_id": str(club.id),
                "pod_lead_id": str(lead.id),
                "min_size": 5,
                "max_size": 2,
            },
        )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Admin: POST /admin/members/pods/{id}/members + capacity rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_add_member_to_pod_succeeds(members_client, db_session):
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=lead.id)
    new_member = MemberFactory.create()
    db_session.add_all([club, lead, pod, new_member])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"], patches["reconcile"] as mock_reconcile:
        response = await members_client.post(
            f"/admin/members/pods/{pod.id}/members",
            json={"member_id": str(new_member.id)},
        )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["member_id"] == str(new_member.id)
    assert data["assigned_by"] == "admin"
    mock_reconcile.assert_awaited_once()
    assert mock_reconcile.call_args.kwargs["action"] == "add"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_add_member_to_full_pod_returns_409(members_client, db_session):
    """Pod at max_size refuses new members."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=lead.id, max_size=2)
    db_session.add_all([club, lead, pod])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"], patches["reconcile"]:
        for _ in range(2):
            m = MemberFactory.create()
            db_session.add(m)
            await db_session.commit()
            await members_client.post(
                f"/admin/members/pods/{pod.id}/members",
                json={"member_id": str(m.id)},
            )

        # Third add should fail with 409
        overflow = MemberFactory.create()
        db_session.add(overflow)
        await db_session.commit()
        response = await members_client.post(
            f"/admin/members/pods/{pod.id}/members",
            json={"member_id": str(overflow.id)},
        )

    assert response.status_code == 409
    assert "full" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_add_member_already_in_another_pod_returns_409(
    members_client, db_session
):
    """A member can be in at most one active pod."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead1 = MemberFactory.create()
    lead2 = MemberFactory.create()
    pod_a = PodFactory.create(club_id=club.id, pod_lead_id=lead1.id)
    pod_b = PodFactory.create(club_id=club.id, pod_lead_id=lead2.id)
    member = MemberFactory.create()
    db_session.add_all([club, lead1, lead2, pod_a, pod_b, member])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"], patches["reconcile"]:
        # Add to pod A
        r1 = await members_client.post(
            f"/admin/members/pods/{pod_a.id}/members",
            json={"member_id": str(member.id)},
        )
        assert r1.status_code == 201

        # Try to add to pod B — should fail
        r2 = await members_client.post(
            f"/admin/members/pods/{pod_b.id}/members",
            json={"member_id": str(member.id)},
        )

    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Admin: dissolve + extend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_dissolve_pod_marks_inactive_and_reconciles_each_member(
    members_client, db_session
):
    """Dissolve sets status=inactive, soft-leaves all members, and fires
    one chat reconcile (action=remove) per active member."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=lead.id)
    members = [MemberFactory.create() for _ in range(3)]
    db_session.add_all([club, lead, pod, *members])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"], patches["reconcile"] as mock_reconcile:
        for m in members:
            await members_client.post(
                f"/admin/members/pods/{pod.id}/members",
                json={"member_id": str(m.id)},
            )
        # Reset the mock so we only count the dissolve reconciles
        mock_reconcile.reset_mock()

        response = await members_client.post(
            f"/admin/members/pods/{pod.id}/dissolve"
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "inactive"
    assert data["dissolved_at"] is not None
    assert data["active_member_count"] == 0
    # One reconcile per previously-active member, all action=remove
    assert mock_reconcile.await_count == 3
    for call in mock_reconcile.await_args_list:
        assert call.kwargs["action"] == "remove"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_extend_resets_review_window(members_client, db_session):
    """Extend bumps cycle_started_at to now and review_due_at to now+90d."""
    from datetime import datetime, timedelta, timezone

    club = ClubFactory.create()
    lead = MemberFactory.create()
    # Pod with cycle started 80 days ago — review_due_at is 10 days out
    old_cycle = datetime.now(timezone.utc) - timedelta(days=80)
    pod = PodFactory.create(
        club_id=club.id, pod_lead_id=lead.id, cycle_started_at=old_cycle
    )
    db_session.add_all([club, lead, pod])
    await db_session.commit()

    response = await members_client.post(f"/admin/members/pods/{pod.id}/extend")

    assert response.status_code == 200, response.text
    data = response.json()
    new_started = datetime.fromisoformat(data["cycle_started_at"])
    new_review = datetime.fromisoformat(data["review_due_at"])
    # Cycle reset to now (within a few seconds)
    assert (datetime.now(timezone.utc) - new_started).total_seconds() < 10
    # Review window now ~90 days out
    delta_days = (new_review - new_started).days
    assert 89 <= delta_days <= 91


# ---------------------------------------------------------------------------
# Admin: review queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_review_queue_includes_due_pods_only(members_client, db_session):
    """review-queue surfaces only active pods with review_due_at <= now."""
    from datetime import datetime, timedelta, timezone

    club = ClubFactory.create()
    lead = MemberFactory.create()
    overdue = PodFactory.create(
        club_id=club.id,
        pod_lead_id=lead.id,
        cycle_started_at=datetime.now(timezone.utc) - timedelta(days=120),
    )
    fresh = PodFactory.create(
        club_id=club.id,
        pod_lead_id=lead.id,
        cycle_started_at=datetime.now(timezone.utc),
    )
    db_session.add_all([club, lead, overdue, fresh])
    await db_session.commit()

    response = await members_client.get("/admin/members/pods/review-queue")

    assert response.status_code == 200
    pod_ids = {p["id"] for p in response.json()}
    assert str(overdue.id) in pod_ids
    assert str(fresh.id) not in pod_ids


# ---------------------------------------------------------------------------
# Member: GET /members/pods/me + /public + join + leave
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_get_my_pod_returns_null_when_not_in_pod(
    members_client, db_session
):
    """A member with no pod assignment gets null from /me."""
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    db_session.add(member)
    await db_session.commit()

    from services.members_service.app.main import app

    with override_auth(app, user):
        response = await members_client.get("/members/pods/me")

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_join_public_pod_with_capacity(members_client, db_session):
    """Authenticated member can self-join a public+active pod."""
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=lead.id)
    db_session.add_all([member, club, lead, pod])
    await db_session.commit()

    from services.members_service.app.main import app

    patches = _silence_chat_sync()
    with override_auth(app, user), patches["ensure"], patches["reconcile"] as mr:
        response = await members_client.post(f"/members/pods/{pod.id}/join")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["member_id"] == str(member.id)
    assert data["assigned_by"] == "self"
    mr.assert_awaited_once()
    assert mr.call_args.kwargs["action"] == "add"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_join_private_pod_returns_403(members_client, db_session):
    """Self-join refuses private pods — admin assignment is required."""
    from services.members_service.models import PodVisibility

    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(
        club_id=club.id,
        pod_lead_id=lead.id,
        visibility=PodVisibility.PRIVATE,
    )
    db_session.add_all([member, club, lead, pod])
    await db_session.commit()

    from services.members_service.app.main import app

    with override_auth(app, user), patch(_ENSURE_CHAT, new_callable=AsyncMock):
        response = await members_client.post(f"/members/pods/{pod.id}/join")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_leave_pod_softleaves_assignment(members_client, db_session):
    """leave fires chat reconcile remove and frees the pod slot."""
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=lead.id)
    db_session.add_all([member, club, lead, pod])
    await db_session.commit()

    from services.members_service.app.main import app

    patches = _silence_chat_sync()
    with override_auth(app, user), patches["ensure"], patches["reconcile"] as mr:
        # Join first
        join = await members_client.post(f"/members/pods/{pod.id}/join")
        assert join.status_code == 201
        mr.reset_mock()

        # Now leave
        leave = await members_client.post("/members/pods/me/leave")

    assert leave.status_code == 204
    mr.assert_awaited_once()
    assert mr.call_args.kwargs["action"] == "remove"

    # Confirm I'm no longer in any pod
    with override_auth(app, user):
        me = await members_client.get("/members/pods/me")
    assert me.status_code == 200
    assert me.json() is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_list_public_pods_filters_by_club(members_client, db_session):
    """/members/pods/public?club_id=X returns only that club's public+active pods."""
    user = make_member_user()
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    club_a = ClubFactory.create()
    club_b = ClubFactory.create()
    lead = MemberFactory.create()
    pod_a = PodFactory.create(club_id=club_a.id, pod_lead_id=lead.id)
    pod_b = PodFactory.create(club_id=club_b.id, pod_lead_id=lead.id)
    db_session.add_all([member, club_a, club_b, lead, pod_a, pod_b])
    await db_session.commit()

    from services.members_service.app.main import app

    with override_auth(app, user):
        response = await members_client.get(
            f"/members/pods/public?club_id={club_a.id}"
        )

    assert response.status_code == 200
    pod_ids = {p["id"] for p in response.json()}
    assert str(pod_a.id) in pod_ids
    assert str(pod_b.id) not in pod_ids


# ---------------------------------------------------------------------------
# Admin: transfer member between pods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_transfer_member_to_another_pod(members_client, db_session):
    """Transfer soft-leaves the source assignment, creates a new one with
    assigned_by=lead_transfer, and fires reconcile remove + add."""
    await _setup_admin_with_member(db_session)
    club = ClubFactory.create()
    lead1 = MemberFactory.create()
    lead2 = MemberFactory.create()
    pod_src = PodFactory.create(club_id=club.id, pod_lead_id=lead1.id)
    pod_tgt = PodFactory.create(club_id=club.id, pod_lead_id=lead2.id)
    member = MemberFactory.create()
    db_session.add_all([club, lead1, lead2, pod_src, pod_tgt, member])
    await db_session.commit()

    patches = _silence_chat_sync()
    with patches["ensure"], patches["reconcile"] as mr:
        # Place member into source pod
        await members_client.post(
            f"/admin/members/pods/{pod_src.id}/members",
            json={"member_id": str(member.id)},
        )
        mr.reset_mock()

        # Transfer
        response = await members_client.post(
            f"/admin/members/pods/{pod_src.id}/transfers"
            f"?member_id={member.id}",
            json={"target_pod_id": str(pod_tgt.id)},
        )

    assert response.status_code == 204
    # Two reconcile calls: remove from src, add to target
    assert mr.await_count == 2
    actions = [c.kwargs["action"] for c in mr.await_args_list]
    assert actions == ["remove", "add"]


# ---------------------------------------------------------------------------
# Internal: /internal/members/pods/* (sessions ↔ pods read-time integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_get_pod_returns_schedule_and_active_members(
    members_client, db_session
):
    """Sessions service uses GET /internal/members/pods/{id} to fetch a
    pod's schedule + active member roster when scheduling a Club session."""
    from datetime import time

    from services.members_service.models import DayOfWeek, PodAssignmentSource

    club = ClubFactory.create()
    lead = MemberFactory.create()
    pod = PodFactory.create(
        club_id=club.id,
        pod_lead_id=lead.id,
        handle="orcas",
        default_session_day=DayOfWeek.WED,
        default_session_time=time(18, 0),
        default_session_duration_minutes=120,
    )
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    db_session.add_all([club, lead, pod, m1, m2])
    await db_session.commit()

    # Seed two active assignments directly so we don't need to mock chat-sync.
    from tests.factories import PodAssignmentFactory

    a1 = PodAssignmentFactory.create(
        pod_id=pod.id, member_id=m1.id, assigned_by=PodAssignmentSource.SELF
    )
    a2 = PodAssignmentFactory.create(
        pod_id=pod.id, member_id=m2.id, assigned_by=PodAssignmentSource.ADMIN
    )
    db_session.add_all([a1, a2])
    await db_session.commit()

    response = await members_client.get(f"/internal/members/pods/{pod.id}")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"] == str(pod.id)
    assert data["club_id"] == str(club.id)
    assert data["handle"] == "orcas"
    assert data["pod_lead_id"] == str(lead.id)
    assert data["default_session_day"] == "wed"
    assert data["default_session_time"].startswith("18:00")
    assert data["default_session_duration_minutes"] == 120
    assert data["active_member_count"] == 2
    assert set(data["active_member_ids"]) == {str(m1.id), str(m2.id)}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_get_pod_404_on_missing(members_client, db_session):
    response = await members_client.get(
        f"/internal/members/pods/{uuid.uuid4()}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_list_pods_filters_by_club_and_status(
    members_client, db_session
):
    """sessions_service uses GET /internal/members/pods?club_id=X to do
    batch scheduling — defaults to active pods only."""
    from services.members_service.models import PodStatus

    club_a = ClubFactory.create()
    club_b = ClubFactory.create()
    lead = MemberFactory.create()
    p1 = PodFactory.create(club_id=club_a.id, pod_lead_id=lead.id)
    p2 = PodFactory.create(club_id=club_a.id, pod_lead_id=lead.id)
    p_inactive = PodFactory.create(
        club_id=club_a.id, pod_lead_id=lead.id, status=PodStatus.INACTIVE
    )
    p_other = PodFactory.create(club_id=club_b.id, pod_lead_id=lead.id)
    db_session.add_all([club_a, club_b, lead, p1, p2, p_inactive, p_other])
    await db_session.commit()

    # Default: active pods in club_a only
    response = await members_client.get(
        f"/internal/members/pods?club_id={club_a.id}"
    )
    assert response.status_code == 200, response.text
    ids = {p["id"] for p in response.json()}
    assert ids == {str(p1.id), str(p2.id)}

    # status=all includes the inactive one
    response_all = await members_client.get(
        f"/internal/members/pods?club_id={club_a.id}&status=all"
    )
    assert response_all.status_code == 200
    ids_all = {p["id"] for p in response_all.json()}
    assert str(p_inactive.id) in ids_all
