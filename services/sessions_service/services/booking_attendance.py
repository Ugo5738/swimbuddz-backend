"""Synchronize confirmed booking state into attendance-service."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.logging import get_logger
from services.sessions_service.models import SessionBooking

logger = get_logger(__name__)


async def sync_booking_attendance(
    booking: SessionBooking,
    *,
    attendance_status: str = "present",
) -> None:
    """Idempotently upsert the member attendance row for a booking."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.ATTENDANCE_SERVICE_URL}"
                f"/attendance/sessions/{booking.session_id}/attendance/public",
                json={
                    "member_id": str(booking.member_id),
                    "status": attendance_status,
                    "role": "swimmer",
                    "notes": (
                        f"Booking {booking.id} confirmed; default attendance"
                        if attendance_status == "present"
                        else f"Booking {booking.id} cancelled"
                    ),
                },
                headers={"Authorization": f"Bearer {_service_role_jwt('sessions')}"},
            )
            response.raise_for_status()
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.error(
            "Booking attendance sync failed for booking=%s status=%s: %s",
            booking.id,
            attendance_status,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The booking was saved, but attendance could not be synchronized. "
                "Retry this request before continuing."
            ),
        ) from exc
