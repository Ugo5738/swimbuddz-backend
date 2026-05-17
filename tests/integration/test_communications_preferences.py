"""Integration tests for communications_service/routers/preferences.py.

History: these tests were originally added during the cross-user-isolation
audit to document a systemic bug — three preferences endpoints used
`current_user.id` (which doesn't exist on AuthUser) AND compared a
DB-internal Member.id to the Supabase auth_id (mismatched UUID types).
Every call returned 500.

That fix has now landed:
  * `current_user.id` → `current_user.user_id` everywhere
  * `NotificationPreferences.member_id` (UUID) → `member_auth_id` (string)
    — migration `785e73dd9714` swaps the column + the unique index
  * `GET /preferences/{member_id}` and `POST /preferences/check-opt-in`
    deleted entirely (one was always-broken, the other had zero callers
    and no auth)

These tests now describe the *post-fix* expected behaviour. They are
marked `xfail` only because the migration has not yet been applied to
the shared dev DB (no `reset.sh` run yet). Once `./scripts/db/reset.sh
dev` lands the schema, every test below should pass and the xfail
markers can be removed in a follow-up commit.
"""

import uuid

import pytest


# Migration 785e73dd9714 (member_id UUID → member_auth_id string) has been
# applied. xfail marker removed.


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_my_preferences_auto_creates_defaults_on_first_access(
    communications_client, db_session
):
    """First call to /me returns default prefs (and persists a row)."""
    response = await communications_client.get("/preferences/me")
    assert response.status_code == 200, response.text
    body = response.json()
    # Defaults from the model
    assert body["email_announcements"] is True
    assert body["email_marketing"] is False
    # Auth-id surfaced (was previously a UUID member_id)
    assert isinstance(body["member_auth_id"], str)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_my_preferences_persists_updates(communications_client, db_session):
    """PATCH /me updates the fields in the body; unset fields keep prior values."""
    # Touch the row first so it exists with defaults
    await communications_client.get("/preferences/me")

    response = await communications_client.patch(
        "/preferences/me",
        json={"email_marketing": True, "weekly_digest": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email_marketing"] is True
    assert body["weekly_digest"] is False
    # Unset field keeps its default
    assert body["email_announcements"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_my_preferences_auto_creates_row_for_first_time_user(
    communications_client, db_session
):
    """If the member has never read their prefs, PATCH must still work
    (no chicken-and-egg requirement to GET first).
    """
    response = await communications_client.patch(
        "/preferences/me",
        json={"email_session_reminders": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["email_session_reminders"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_check_opt_in_endpoint_is_gone(communications_client):
    """The unauthenticated /check-opt-in endpoint was removed (no auth,
    no callers). Confirm it 404s rather than silently allowing access.
    """
    response = await communications_client.post(
        "/preferences/check-opt-in",
        params={"member_id": str(uuid.uuid4()), "notification_type": "email_marketing"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preferences_by_member_id_endpoint_is_gone(communications_client):
    """The GET /preferences/{member_id} endpoint was removed (always
    broken). Confirm it 404s.
    """
    response = await communications_client.get(
        f"/preferences/{uuid.uuid4()}",
    )
    assert response.status_code == 404
