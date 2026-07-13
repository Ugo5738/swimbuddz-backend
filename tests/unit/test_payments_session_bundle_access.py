from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.payments_service.routers.intents import intent_creation


def _session(**overrides):
    data = {
        "id": "session-1",
        "title": "Community Swim",
        "session_type": "community",
        "status": "scheduled",
        "starts_at": "2035-01-01T09:00:00+00:00",
        "ends_at": "2035-01-01T10:00:00+00:00",
        "cohort_id": None,
        "pod_id": None,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_session_bundle_access_allows_paid_member(monkeypatch):
    monkeypatch.setattr(
        intent_creation,
        "get_member_by_auth_id",
        AsyncMock(return_value={"id": "member-1"}),
    )
    monkeypatch.setattr(
        intent_creation,
        "get_member_membership",
        AsyncMock(
            return_value={
                "member_id": "member-1",
                "primary_tier": "community",
                "active_tiers": ["community"],
                "community_paid_until": "2035-01-01T00:00:00+00:00",
                "club_paid_until": None,
                "academy_paid_until": None,
            }
        ),
    )
    monkeypatch.setattr(
        intent_creation,
        "get_session_by_id",
        AsyncMock(return_value=_session()),
    )

    await intent_creation._validate_session_bundle_access(
        member_auth_id="auth-1",
        session_ids=["session-1"],
    )


@pytest.mark.asyncio
async def test_session_bundle_access_rejects_unpaid_member(monkeypatch):
    monkeypatch.setattr(
        intent_creation,
        "get_member_by_auth_id",
        AsyncMock(return_value={"id": "member-1"}),
    )
    monkeypatch.setattr(
        intent_creation,
        "get_member_membership",
        AsyncMock(
            return_value={
                "member_id": "member-1",
                "primary_tier": "community",
                "active_tiers": ["community"],
                "community_paid_until": None,
                "club_paid_until": None,
                "academy_paid_until": None,
            }
        ),
    )
    monkeypatch.setattr(
        intent_creation,
        "get_session_by_id",
        AsyncMock(return_value=_session()),
    )

    with pytest.raises(HTTPException) as exc:
        await intent_creation._validate_session_bundle_access(
            member_auth_id="auth-1",
            session_ids=["session-1"],
        )

    assert exc.value.status_code == 403
    assert "active SwimBuddz membership" in exc.value.detail
