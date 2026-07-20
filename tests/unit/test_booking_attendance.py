"""Booking confirmation to attendance-service synchronization."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import services.sessions_service.services.booking_attendance as attendance_sync


class _Client:
    def __init__(self, response):
        self.response = response
        self.post = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _booking():
    return SimpleNamespace(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_confirmed_booking_upserts_default_present_attendance(monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None)
    client = _Client(response)
    monkeypatch.setattr(attendance_sync.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(attendance_sync, "_service_role_jwt", lambda _service: "jwt")
    monkeypatch.setattr(
        attendance_sync,
        "get_settings",
        lambda: SimpleNamespace(ATTENDANCE_SERVICE_URL="http://attendance"),
    )
    booking = _booking()

    await attendance_sync.sync_booking_attendance(booking)

    client.post.assert_awaited_once_with(
        f"http://attendance/attendance/sessions/{booking.session_id}/attendance/public",
        json={
            "member_id": str(booking.member_id),
            "status": "present",
            "role": "swimmer",
            "notes": f"Booking {booking.id} confirmed; default attendance",
        },
        headers={"Authorization": "Bearer jwt"},
    )


@pytest.mark.asyncio
async def test_attendance_sync_failure_is_retryable_upstream(monkeypatch):
    request = httpx.Request("POST", "http://attendance/attendance")

    def fail():
        raise httpx.ConnectError("unavailable", request=request)

    client = _Client(SimpleNamespace(raise_for_status=fail))
    monkeypatch.setattr(attendance_sync.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(attendance_sync, "_service_role_jwt", lambda _service: "jwt")
    monkeypatch.setattr(
        attendance_sync,
        "get_settings",
        lambda: SimpleNamespace(ATTENDANCE_SERVICE_URL="http://attendance"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await attendance_sync.sync_booking_attendance(_booking())

    assert exc_info.value.status_code == 502
    assert "Retry this request" in exc_info.value.detail
