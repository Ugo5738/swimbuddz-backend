"""Regression tests for communications_service/routers/preferences.py.

Discovered during the cross-user-isolation audit: three endpoints in
this router reference `current_user.id`, an attribute that does not
exist on `libs.auth.models.AuthUser` (which exposes `user_id`).

  - GET   /preferences/me            (line 32, 39)
  - PATCH /preferences/me            (line 57, 65)
  - GET   /preferences/{member_id}   (line 91)

Every call to any of these endpoints raises `AttributeError`, surfacing
to the client as a 500. The feature is currently non-functional.

The fix is non-trivial — `NotificationPreferences.member_id` is a UUID
column, but `AuthUser.user_id` is a string (Supabase auth ID). Replacing
`.id` with `.user_id` would still fail because UUID-typed columns won't
match an auth-id string at the SQL level. A correct fix needs either:
  (a) a Members service lookup to translate auth_id → Member.id, or
  (b) a schema migration to store auth_id as a string column.

These tests are marked xfail so CI documents the bug without blocking;
flip to expected-pass when the fix lands.
"""

import uuid

import pytest


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.xfail(
    reason="preferences.py uses AuthUser.id which doesn't exist (should be user_id); "
    "see module docstring",
    raises=AttributeError,
    strict=False,
)
async def test_get_my_preferences_returns_200_not_500(communications_client):
    """GET /preferences/me should succeed for an authenticated user.

    Currently fails with 500 because of AuthUser.id AttributeError.
    """
    response = await communications_client.get("/preferences/me")
    # The bug surfaces as 500; the assertion below documents the
    # post-fix expected behaviour.
    assert response.status_code != 500, (
        f"500 surfaced from /preferences/me: {response.text}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.xfail(
    reason="PATCH /preferences/me uses AuthUser.id which doesn't exist",
    raises=AttributeError,
    strict=False,
)
async def test_patch_my_preferences_returns_200_not_500(communications_client):
    response = await communications_client.patch(
        "/preferences/me",
        json={"email_announcements": False},
    )
    assert response.status_code != 500, (
        f"500 surfaced from /preferences/me: {response.text}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.xfail(
    reason="GET /preferences/{member_id} uses AuthUser.id which doesn't exist",
    raises=AttributeError,
    strict=False,
)
async def test_get_member_preferences_authz_check_compiles(
    communications_client,
):
    """The endpoint's authz check `if current_user.id != member_id ...`
    crashes before evaluating, so even a legitimate admin caller sees 500.
    """
    fake_member_id = uuid.uuid4()
    response = await communications_client.get(f"/preferences/{fake_member_id}")
    # Expected post-fix: 404 (no prefs exist) or 200 (admin override).
    # Current bug: 500.
    assert response.status_code != 500, (
        f"500 surfaced from /preferences/{{member_id}}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Cross-user-isolation gap: /preferences/check-opt-in takes member_id as a
# query parameter and has NO authentication dependency at all (lines 112-153).
# Any service or client with network reach can probe opt-in status for any
# member. Acceptable if intended for service-to-service use, but the
# preferences-router does not gate it behind a service-role check.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_check_opt_in_currently_has_no_auth_gate(communications_client):
    """Document the current behaviour: the endpoint accepts unauthenticated
    requests. Flip this test if the team decides to require service-role
    auth on it (recommended for closed-loop comms calls only).
    """
    # Probe with a random member_id — no auth header sent
    response = await communications_client.post(
        "/preferences/check-opt-in",
        params={
            "member_id": str(uuid.uuid4()),
            "notification_type": "email_announcements",
        },
    )
    # The fixture wires admin auth via dependency override so we can't
    # easily simulate "no auth"; this is more an audit-checkpoint test
    # than a positive assertion. The point is: there's no Depends() on
    # the route signature beyond the DB session.
    assert response.status_code in (200, 404, 422), response.text
