import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from services.sessions_service.models import SessionType
from services.sessions_service.services.club_scope import require_valid_club_scope


@pytest.mark.asyncio
async def test_general_club_scope_accepts_active_club():
    club_id = uuid.uuid4()
    with patch(
        "services.sessions_service.services.club_scope.get_club_by_id",
        new_callable=AsyncMock,
        return_value={"id": str(club_id), "is_active": True},
    ) as get_club:
        await require_valid_club_scope(
            session_type=SessionType.CLUB,
            club_id=club_id,
            pod_id=None,
        )

    get_club.assert_awaited_once_with(str(club_id), calling_service="sessions")


@pytest.mark.asyncio
async def test_pod_must_belong_to_selected_club():
    club_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    with (
        patch(
            "services.sessions_service.services.club_scope.get_club_by_id",
            new_callable=AsyncMock,
            return_value={"id": str(club_id), "is_active": True},
        ),
        patch(
            "services.sessions_service.services.club_scope.get_pod_by_id",
            new_callable=AsyncMock,
            return_value={
                "id": str(pod_id),
                "club_id": str(uuid.uuid4()),
                "status": "active",
            },
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await require_valid_club_scope(
                session_type=SessionType.CLUB,
                club_id=club_id,
                pod_id=pod_id,
            )

    assert exc_info.value.status_code == 400
    assert "does not belong" in exc_info.value.detail


@pytest.mark.asyncio
async def test_pod_scope_accepts_active_pod_from_selected_club():
    club_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    with (
        patch(
            "services.sessions_service.services.club_scope.get_club_by_id",
            new_callable=AsyncMock,
            return_value={"id": str(club_id), "is_active": True},
        ),
        patch(
            "services.sessions_service.services.club_scope.get_pod_by_id",
            new_callable=AsyncMock,
            return_value={
                "id": str(pod_id),
                "club_id": str(club_id),
                "status": "active",
            },
        ),
    ):
        await require_valid_club_scope(
            session_type=SessionType.CLUB,
            club_id=club_id,
            pod_id=pod_id,
        )


@pytest.mark.asyncio
async def test_club_scope_requires_club_id():
    with pytest.raises(HTTPException) as exc_info:
        await require_valid_club_scope(
            session_type=SessionType.CLUB,
            club_id=None,
            pod_id=None,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_non_club_scope_skips_cross_service_lookup():
    with patch(
        "services.sessions_service.services.club_scope.get_club_by_id",
        new_callable=AsyncMock,
    ) as get_club:
        await require_valid_club_scope(
            session_type=SessionType.COMMUNITY,
            club_id=None,
            pod_id=None,
        )

    get_club.assert_not_awaited()
