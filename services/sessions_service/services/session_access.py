"""Sessions-service adapter for shared session access rules."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import httpx
from fastapi import HTTPException

from libs.common.logging import get_logger
from libs.common.service_client import (
    check_club_access_batch,
    check_cohort_enrollment,
    check_cohort_enrollments_batch,
    get_member_membership,
    get_pod_by_id,
    get_pod_rosters_batch,
)
from libs.common.session_access import evaluate_session_access
from services.sessions_service.models import Session

logger = get_logger(__name__)


def _is_club_session(session: Session) -> bool:
    value = getattr(session.session_type, "value", session.session_type)
    return str(value).lower() == "club"


def _club_access_check(session: Session, member_id: str) -> dict:
    return {
        "context_key": str(session.id),
        "member_id": member_id,
        "at": session.starts_at.isoformat(),
        "pool_id": str(session.pool_id) if session.pool_id else None,
        "pod_id": str(session.pod_id) if session.pod_id else None,
    }


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
    confirmed_booking: bool = False,
):
    """Build session-specific context and evaluate access for one session."""
    member_id = str(member_payload["member_id"])

    cohort_enrollment = None
    if session.cohort_id is not None and not confirmed_booking:
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
    if session.pod_id is not None and not confirmed_booking:
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

    if _is_club_session(session) and not confirmed_booking:
        try:
            club_access = await check_club_access_batch(
                [_club_access_check(session, member_id)],
                calling_service=calling_service,
            )
        except httpx.HTTPError as e:
            logger.warning(
                "check_club_access_batch failed for session=%s member=%s: %s",
                session.id,
                member_id,
                e,
            )
            raise HTTPException(
                status_code=503,
                detail="Could not verify Club access. Please try again.",
            )
        club_access_result = club_access.get(str(session.id)) or {
            "allowed": False,
            "source": "none",
        }
    else:
        club_access_result = None

    return evaluate_session_access(
        member_payload,
        session,
        now=now,
        cohort_enrollment=cohort_enrollment,
        pod_member_ids=pod_member_ids,
        confirmed_booking=confirmed_booking,
        club_access_result=club_access_result,
    )


async def get_sessions_access_context(
    *,
    sessions: list[Session],
    member_payload: dict,
    confirmed_session_ids: set[uuid.UUID],
    calling_service: str = "sessions",
) -> tuple[dict[str, dict], dict[str, list[str]], dict[str, dict]]:
    """Batch all cross-service context needed to evaluate a session list."""
    cohort_ids = sorted(
        {
            str(session.cohort_id)
            for session in sessions
            if session.cohort_id is not None and session.id not in confirmed_session_ids
        }
    )
    pod_ids = sorted(
        {
            str(session.pod_id)
            for session in sessions
            if session.pod_id is not None and session.id not in confirmed_session_ids
        }
    )
    member_id = str(member_payload["member_id"])
    club_checks = [
        _club_access_check(session, member_id)
        for session in sessions
        if _is_club_session(session) and session.id not in confirmed_session_ids
    ]

    try:
        cohort_access, pod_rosters, club_access = await asyncio.gather(
            check_cohort_enrollments_batch(
                cohort_ids,
                member_id,
                calling_service=calling_service,
            ),
            get_pod_rosters_batch(
                pod_ids,
                calling_service=calling_service,
            ),
            check_club_access_batch(
                club_checks,
                calling_service=calling_service,
            ),
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Batch session access context failed for member=%s: %s",
            member_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Could not verify session access. Please try again.",
        ) from exc

    return cohort_access, pod_rosters, club_access


def evaluate_session_access_from_context(
    *,
    session: Session,
    member_payload: dict,
    now: datetime,
    confirmed_booking: bool,
    cohort_access: dict[str, dict],
    pod_rosters: dict[str, list[str]],
    club_access: dict[str, dict],
):
    """Evaluate one list item using already-batched cross-service context."""
    cohort_enrollment = None
    if session.cohort_id is not None and not confirmed_booking:
        cohort_enrollment = cohort_access.get(
            str(session.cohort_id),
            {
                "enrolled": False,
                "status": None,
                "access_suspended": False,
            },
        )

    pod_member_ids = None
    if session.pod_id is not None and not confirmed_booking:
        pod_member_ids = pod_rosters.get(str(session.pod_id), [])

    club_access_result = None
    if _is_club_session(session) and not confirmed_booking:
        club_access_result = club_access.get(str(session.id)) or {
            "allowed": False,
            "source": "none",
        }

    return evaluate_session_access(
        member_payload,
        session,
        now=now,
        cohort_enrollment=cohort_enrollment,
        pod_member_ids=pod_member_ids,
        confirmed_booking=confirmed_booking,
        club_access_result=club_access_result,
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
