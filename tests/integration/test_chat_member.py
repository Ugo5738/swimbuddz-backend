"""Integration tests for chat_service member-facing endpoints.

These test the member-facing API (`/chat/*`) against a fresh test DB.
auth_id → member_id resolution and the moderation / notification
side-effects are mocked at the import site (per the "patch where it's
called from" rule documented in MEMORY.md)."""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

# Where the chat code IMPORTS these helpers from. Patch the local reference,
# not the libs path — the function reference is already copied into the
# router/service module's namespace at import time.
_RESOLVE_MEMBER_PATH_MEMBER = (
    "services.chat_service.routers.member.get_member_by_auth_id"
)
_DISPATCH_NOTIFICATION_PATH = (
    "services.chat_service.services.message_ops.dispatch_notification"
)
_MODERATE_TEXT_PATH = "services.chat_service.services.message_ops.moderate_text"


def _mock_member_lookup(member_id: str | None = None):
    """Build a patch context that fakes get_member_by_auth_id for the
    chat member router. Returns a (patcher, member_id) pair."""
    mid = member_id or str(uuid.uuid4())
    return (
        patch(
            _RESOLVE_MEMBER_PATH_MEMBER,
            new_callable=AsyncMock,
            return_value={"id": mid},
        ),
        mid,
    )


@asynccontextmanager
async def _silenced_externals():
    """Patch out moderation + notification fan-out.

    Tests don't need a working OpenAI key or communications service. We
    return a benign moderation result and a no-op for notifications."""
    from libs.moderation.types import ModerationResult

    with (
        patch(
            _DISPATCH_NOTIFICATION_PATH,
            new_callable=AsyncMock,
            return_value={"dispatched": 0},
        ),
        patch(
            _MODERATE_TEXT_PATH,
            new_callable=AsyncMock,
            return_value=ModerationResult(flagged=False, labels=[], provider="test"),
        ),
    ):
        yield


async def _provision_channel_with_member(
    chat_client, member_id: str, *, parent_id: str | None = None
) -> str:
    """Helper: create a channel and add the given member. Returns channel_id."""
    parent = parent_id or str(uuid.uuid4())
    ensure = await chat_client.post(
        "/internal/chat/channels/ensure",
        json={
            "type": "group",
            "parent_entity_type": "cohort",
            "parent_entity_id": parent,
            "name": "Test Cohort Channel",
            "retention_policy": "cohort",
        },
    )
    assert ensure.status_code == 200, ensure.text
    channel_id = ensure.json()["channel_id"]

    add = await chat_client.post(
        "/internal/chat/memberships/reconcile",
        json={
            "channel_id": channel_id,
            "member_id": member_id,
            "action": "add",
            "role": "member",
            "derived_from": "enrollment",
        },
    )
    assert add.status_code == 200, add.text
    return channel_id


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_my_channels_returns_only_active_memberships(chat_client):
    """Tests hit the service directly (no gateway prefix), so use /chat/."""
    patcher, mid = _mock_member_lookup()
    with patcher:
        # No channel yet → empty list.
        resp = await chat_client.get("/chat/channels")
        assert resp.status_code == 200
        assert resp.json() == []

        await _provision_channel_with_member(chat_client, mid)

        resp2 = await chat_client.get("/chat/channels")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_channel_requires_membership(chat_client):
    """Non-members get 403 even if the channel exists."""
    patcher, _other_mid = _mock_member_lookup()
    other_member = str(uuid.uuid4())
    channel_id = await _provision_channel_with_member(chat_client, other_member)

    with patcher:  # caller is _another_ member, not in the channel
        resp = await chat_client.get(f"/chat/channels/{channel_id}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Send / list / edit / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_message_idempotent_via_client_id(chat_client):
    """Reusing the same client_message_id returns the same row, not a
    duplicate. This is the offline-retry contract from design §7.3."""
    patcher, mid = _mock_member_lookup()

    async with _silenced_externals():
        with patcher:
            channel_id = await _provision_channel_with_member(chat_client, mid)
            client_msg_id = str(uuid.uuid4())
            payload = {
                "body": "hello there",
                "client_message_id": client_msg_id,
            }

            first = await chat_client.post(
                f"/chat/channels/{channel_id}/messages", json=payload
            )
            assert first.status_code == 201, first.text
            first_id = first.json()["id"]

            second = await chat_client.post(
                f"/chat/channels/{channel_id}/messages", json=payload
            )
            assert second.status_code == 201
            assert second.json()["id"] == first_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_message_lifecycle_send_edit_delete(chat_client):
    patcher, mid = _mock_member_lookup()

    async with _silenced_externals():
        with patcher:
            channel_id = await _provision_channel_with_member(chat_client, mid)
            send = await chat_client.post(
                f"/chat/channels/{channel_id}/messages",
                json={
                    "body": "first draft",
                    "client_message_id": str(uuid.uuid4()),
                },
            )
            assert send.status_code == 201
            msg_id = send.json()["id"]

            edited = await chat_client.patch(
                f"/chat/messages/{msg_id}", json={"body": "final draft"}
            )
            assert edited.status_code == 200
            assert edited.json()["body"] == "final draft"
            assert edited.json()["edited_at"] is not None

            deleted = await chat_client.delete(f"/chat/messages/{msg_id}")
            assert deleted.status_code == 200
            data = deleted.json()
            assert data["deleted_at"] is not None
            # Soft-delete: body becomes the placeholder string.
            assert data["body"] == "[deleted]"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_text_moderation_flags_message(chat_client, db_session):
    """When OpenAI flags the body, the row is still delivered but tagged
    FLAGGED in `safeguarding_review_state` so it surfaces in the queue.
    Design rule: never auto-delete."""
    from sqlalchemy import select

    from libs.moderation.types import (
        ModerationCategory,
        ModerationLabel,
        ModerationResult,
    )
    from services.chat_service.models import ChatMessage

    patcher, mid = _mock_member_lookup()

    flagged = ModerationResult(
        flagged=True,
        labels=[
            ModerationLabel(
                category=ModerationCategory.HARASSMENT,
                confidence=0.92,
                provider_label="harassment",
            )
        ],
        provider="test",
    )

    with (
        patch(
            _DISPATCH_NOTIFICATION_PATH,
            new_callable=AsyncMock,
            return_value={"dispatched": 0},
        ),
        patch(
            _MODERATE_TEXT_PATH,
            new_callable=AsyncMock,
            return_value=flagged,
        ),
        patcher,
    ):
        channel_id = await _provision_channel_with_member(chat_client, mid)
        resp = await chat_client.post(
            f"/chat/channels/{channel_id}/messages",
            json={
                "body": "obnoxious content",
                "client_message_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 201
        msg_id = uuid.UUID(resp.json()["id"])

    # Read the row directly — review_state is intentionally not in the
    # member-facing response shape (admin-only field per design §4.1).
    row = (
        await db_session.execute(select(ChatMessage).where(ChatMessage.id == msg_id))
    ).scalar_one()
    assert row.safeguarding_review_state.value == "flagged"


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reaction_add_remove_idempotent(chat_client):
    patcher, mid = _mock_member_lookup()

    async with _silenced_externals():
        with patcher:
            channel_id = await _provision_channel_with_member(chat_client, mid)
            send = await chat_client.post(
                f"/chat/channels/{channel_id}/messages",
                json={
                    "body": "react to me",
                    "client_message_id": str(uuid.uuid4()),
                },
            )
            msg_id = send.json()["id"]

            r1 = await chat_client.post(
                f"/chat/messages/{msg_id}/reactions",
                json={"emoji": "👍"},
            )
            assert r1.status_code == 201
            reactions = r1.json()["reactions"]
            assert len(reactions) == 1
            assert reactions[0]["emoji"] == "👍"
            assert reactions[0]["count"] == 1
            assert reactions[0]["reacted_by_me"] is True

            # Adding the same reaction again is a no-op (still count=1).
            r2 = await chat_client.post(
                f"/chat/messages/{msg_id}/reactions",
                json={"emoji": "👍"},
            )
            assert r2.status_code == 201
            assert r2.json()["reactions"][0]["count"] == 1

            # Disallowed emoji rejected.
            r3 = await chat_client.post(
                f"/chat/messages/{msg_id}/reactions",
                json={"emoji": "💩"},
            )
            assert r3.status_code == 400

            # Remove.
            r4 = await chat_client.delete(
                f"/chat/messages/{msg_id}/reactions/%F0%9F%91%8D"
            )
            assert r4.status_code == 200
            assert r4.json()["reactions"] == []


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_report_message_collapses_repeat(chat_client):
    """Re-reporting an already-open report from the same reporter returns
    the existing row rather than multiplying the queue."""
    patcher, mid = _mock_member_lookup()

    async with _silenced_externals():
        with patcher:
            channel_id = await _provision_channel_with_member(chat_client, mid)
            send = await chat_client.post(
                f"/chat/channels/{channel_id}/messages",
                json={
                    "body": "needs review",
                    "client_message_id": str(uuid.uuid4()),
                },
            )
            msg_id = send.json()["id"]

            first = await chat_client.post(
                f"/chat/messages/{msg_id}/reports",
                json={"reason": "harassment", "note": "noted once"},
            )
            assert first.status_code == 201
            first_id = first.json()["id"]

            second = await chat_client.post(
                f"/chat/messages/{msg_id}/reports",
                json={"reason": "harassment", "note": "tried twice"},
            )
            assert second.status_code == 201
            assert second.json()["id"] == first_id  # collapsed
