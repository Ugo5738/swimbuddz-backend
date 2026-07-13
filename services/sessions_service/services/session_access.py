"""Sessions-service adapter for shared session access rules."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
from fastapi import HTTPException

from libs.common.logging import get_logger
from libs.common.service_client import (
    check_cohort_enrollment,
    get_member_membership,
    get_pod_by_id,
)
from libs.common.session_access import evaluate_session_access
from services.sessions_service.models import Session

logger = get_logger(__name__)


async def get_member_session_access_payload(
    *,
    member_id: uuid.UUID,
    calling_service: str = "sessions",
) -> dict:
    """Fetch the membership payload used by the shared access evaluator."""
    try:
        membership = await get_member_membership(
            str(member_id), calling_service=calling_service
        )
    except httpx.HTTPError as e:
        logger.warning(
            "get_member_membership failed for member=%s: %s",
            member_id,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not verify your membership. Please try again.",
        )

    return {
        "id": str(member_id),
        "member_id": str(member_id),
        **(membership or {}),
    }


async def evaluate_session_access_for_member(
    *,
    session: Session,
    member_payload: dict,
    now: datetime,
    calling_service: str = "sessions",
):
    """Build session-specific context and evaluate access for one session."""
    member_id = str(member_payload["member_id"])

    cohort_enrollment = None
    if session.cohort_id is not None:
        try:
            cohort_enrollment = await check_cohort_enrollment(
                cohort_id=str(session.cohort_id),
                member_id=member_id,
                calling_service=calling_service,
            )
        except httpx.HTTPError as e:
            logger.warning(
                "check_cohort_enrollment failed for session=%s member=%s: %s",
                session.id,
                member_id,
                e,
            )
            raise HTTPException(
                status_code=503,
                detail="Could not verify cohort enrollment. Please try again.",
            )

    pod_member_ids = None
    if session.pod_id is not None:
        try:
            pod = await get_pod_by_id(
                str(session.pod_id), calling_service=calling_service
            )
        except httpx.HTTPError as e:
            logger.warning(
                "get_pod_by_id failed for session=%s pod=%s member=%s: %s",
                session.id,
                session.pod_id,
                member_id,
                e,
            )
            raise HTTPException(
                status_code=503,
                detail="Could not verify club pod access. Please try again.",
            )
        pod_member_ids = (pod or {}).get("active_member_ids") or []

    return evaluate_session_access(
        member_payload,
        session,
        now=now,
        cohort_enrollment=cohort_enrollment,
        pod_member_ids=pod_member_ids,
    )


async def evaluate_member_session_access(
    *,
    session: Session,
    member_id: uuid.UUID,
    now: datetime,
    calling_service: str = "sessions",
):
    """Fetch member context and evaluate access for one session."""
    member_payload = await get_member_session_access_payload(
        member_id=member_id,
        calling_service=calling_service,
    )
    return await evaluate_session_access_for_member(
        session=session,
        member_payload=member_payload,
        now=now,
        calling_service=calling_service,
    )
