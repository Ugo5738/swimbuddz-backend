"""Integration tests for chat_service internal s2s endpoints.

These cover the API surface upstream services (academy, events) use to
provision channels and reconcile membership — the contract that the
academy `chat_sync` and events `chat_sync` modules depend on.
"""

import uuid

import pytest


def _ensure_payload(parent_entity_id: str, **overrides):
    """Helper: minimal valid body for /internal/chat/channels/ensure."""
    body = {
        "type": "group",
        "parent_entity_type": "cohort",
        "parent_entity_id": parent_entity_id,
        "name": "Test Cohort",
        "retention_policy": "cohort",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# channels/ensure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_channel_creates_then_idempotent(chat_client):
    """First call creates the channel; second call returns the same id with
    ``created=False``. Idempotency is keyed on (type, parent_entity_*)."""
    parent_id = str(uuid.uuid4())
    body = _ensure_payload(parent_id)

    first = await chat_client.post("/internal/chat/channels/ensure", json=body)
    assert first.status_code == 200, first.text
    first_data = first.json()
    assert first_data["created"] is True
    channel_id = first_data["channel_id"]

    second = await chat_client.post("/internal/chat/channels/ensure", json=body)
    assert second.status_code == 200, second.text
    second_data = second.json()
    assert second_data["created"] is False
    assert second_data["channel_id"] == channel_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_channel_distinguishes_parents(chat_client):
    """Different parent_entity_ids → different channels, even with same name."""
    a = await chat_client.post(
        "/internal/chat/channels/ensure",
        json=_ensure_payload(str(uuid.uuid4()), name="Cohort A"),
    )
    b = await chat_client.post(
        "/internal/chat/channels/ensure",
        json=_ensure_payload(str(uuid.uuid4()), name="Cohort B"),
    )
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json()["channel_id"] != b.json()["channel_id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ensure_channel_creator_is_admin(chat_client, db_session):
    """When `created_by` is provided, that member is added as channel admin."""
    creator_id = str(uuid.uuid4())
    payload = _ensure_payload(str(uuid.uuid4()), created_by=creator_id)
    resp = await chat_client.post("/internal/chat/channels/ensure", json=payload)
    assert resp.status_code == 200
    channel_id = resp.json()["channel_id"]

    # Verify membership directly via the DB session.
    from sqlalchemy import select

    from services.chat_service.models import ChatChannelMember

    result = await db_session.execute(
        select(ChatChannelMember).where(
            ChatChannelMember.channel_id == uuid.UUID(channel_id),
            ChatChannelMember.member_id == uuid.UUID(creator_id),
        )
    )
    membership = result.scalar_one_or_none()
    assert membership is not None, "creator should be added as admin"
    assert membership.role.value == "admin"


# ---------------------------------------------------------------------------
# memberships/reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reconcile_add_then_remove(chat_client, db_session):
    """`add` puts the member in the channel; `remove` soft-leaves them.
    Re-adding after a remove should re-activate the row, not duplicate it."""
    parent_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    enrollment_id = str(uuid.uuid4())

    ensure = await chat_client.post(
        "/internal/chat/channels/ensure", json=_ensure_payload(parent_id)
    )
    assert ensure.status_code == 200
    channel_id = ensure.json()["channel_id"]

    add_payload = {
        "parent_entity_type": "cohort",
        "parent_entity_id": parent_id,
        "member_id": member_id,
        "action": "add",
        "role": "member",
        "derived_from": "enrollment",
        "derivation_ref": enrollment_id,
    }
    add_resp = await chat_client.post(
        "/internal/chat/memberships/reconcile", json=add_payload
    )
    assert add_resp.status_code == 200, add_resp.text
    assert add_resp.json()["action_taken"] == "add"
    assert add_resp.json()["channel_id"] == channel_id

    remove_resp = await chat_client.post(
        "/internal/chat/memberships/reconcile",
        json={**add_payload, "action": "remove"},
    )
    assert remove_resp.status_code == 200
    assert remove_resp.json()["action_taken"] == "remove"

    # Re-add re-activates the row rather than inserting a second.
    re_add = await chat_client.post(
        "/internal/chat/memberships/reconcile", json=add_payload
    )
    assert re_add.status_code == 200

    from sqlalchemy import func, select

    from services.chat_service.models import ChatChannelMember

    count_q = (
        select(func.count())
        .select_from(ChatChannelMember)
        .where(
            ChatChannelMember.channel_id == uuid.UUID(channel_id),
            ChatChannelMember.member_id == uuid.UUID(member_id),
        )
    )
    total = (await db_session.execute(count_q)).scalar()
    assert total == 1, "membership row should be re-activated, not duplicated"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reconcile_requires_channel_or_parent(chat_client):
    """Providing neither channel_id nor parent_entity_* yields 400."""
    bad_payload = {
        "member_id": str(uuid.uuid4()),
        "action": "add",
    }
    resp = await chat_client.post(
        "/internal/chat/memberships/reconcile", json=bad_payload
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reconcile_unknown_parent_returns_404(chat_client):
    """Reconcile against a parent with no channel returns 404 — upstream
    services should call /channels/ensure first."""
    resp = await chat_client.post(
        "/internal/chat/memberships/reconcile",
        json={
            "parent_entity_type": "cohort",
            "parent_entity_id": str(uuid.uuid4()),
            "member_id": str(uuid.uuid4()),
            "action": "add",
        },
    )
    assert resp.status_code == 404
