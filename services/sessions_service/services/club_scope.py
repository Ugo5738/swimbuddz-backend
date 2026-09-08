"""Validation for Club-owned sessions and templates."""

from __future__ import annotations

import uuid

import httpx
from fastapi import HTTPException, status

from libs.common.service_client import get_club_by_id, get_pod_by_id
from services.sessions_service.models import SessionType


async def require_valid_club_scope(
    *,
    session_type: SessionType | str,
    club_id: uuid.UUID | None,
    pod_id: uuid.UUID | None,
) -> None:
    """Validate the cross-service Club → Pod relationship.

    ``club_id`` is the stable owner of every Club session. ``pod_id`` is an
    optional narrower audience and, when present, must belong to that Club.
    """
    session_type_value = (
        session_type.value if hasattr(session_type, "value") else str(session_type)
    )
    if session_type_value != SessionType.CLUB.value:
        return

    if club_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select the Club this session belongs to.",
        )

    try:
        club = await get_club_by_id(str(club_id), calling_service="sessions")
        if club is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid club_id: Club does not exist.",
            )
        if not club.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected Club is inactive.",
            )

        if pod_id is None:
            return

        pod = await get_pod_by_id(str(pod_id), calling_service="sessions")
        if pod is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid pod_id: Pod does not exist.",
            )
        if str(pod.get("club_id")) != str(club_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected Pod does not belong to the selected Club.",
            )
        if pod.get("status") != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected Pod is inactive.",
            )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate the selected Club and Pod.",
        ) from exc


__all__ = ["require_valid_club_scope"]
