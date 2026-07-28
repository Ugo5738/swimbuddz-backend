"""Unified, access-aware calendar assembled from sessions and events."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from libs.auth.dependencies import get_optional_user, is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.gateway_service.app import clients
from services.gateway_service.app.schemas import CalendarItemResponse, CalendarResponse

router = APIRouter(prefix="/calendar", tags=["calendar"])
logger = get_logger(__name__)

_AUDIENCE_ORDER = ("community", "club", "academy")
_MAX_RANGE = timedelta(days=400)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _in_range(value: Any, range_start: datetime, range_end: datetime) -> bool:
    starts_at = _parse_datetime(value)
    if starts_at is None:
        return False
    return range_start <= starts_at < range_end


def _auth_headers(request: Request) -> Optional[dict[str, str]]:
    authorization = request.headers.get("Authorization")
    return {"Authorization": authorization} if authorization else None


async def _fetch_optional(
    client: clients.ServiceClient,
    path: str,
    label: str,
    headers: Optional[dict[str, str]],
) -> tuple[Any, Optional[str]]:
    try:
        response = await client.get(path, headers=headers)
        return response.json(), None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Calendar downstream request failed",
            extra={
                "extra_fields": {
                    "service": label,
                    "status_code": exc.response.status_code,
                }
            },
        )
        return None, f"{label} service error ({exc.response.status_code})"
    except httpx.RequestError as exc:
        logger.error(
            "Calendar downstream service unavailable",
            extra={"extra_fields": {"service": label, "error": str(exc)}},
        )
        return None, f"{label} service unavailable"


def _member_audiences(profile: Any) -> set[str]:
    """Return backend-normalized active audiences for event filtering."""
    if not isinstance(profile, dict):
        return {"community"}
    membership = profile.get("membership")
    if not isinstance(membership, dict):
        return {"community"}

    tiers = membership.get("effective_paid_tiers") or membership.get("paid_tiers") or []
    allowed = {
        str(tier).strip().lower()
        for tier in tiers
        if str(tier).strip().lower() in _AUDIENCE_ORDER
    }
    # Community is the umbrella calendar and remains visible to every visitor.
    allowed.add("community")
    return allowed


def _session_audience(session_type: str) -> str:
    if session_type == "club":
        return "club"
    if session_type in {"academy", "cohort_class"}:
        return "academy"
    return "community"


def _event_audience(tier_access: Any) -> str:
    normalized = str(tier_access or "community").strip().lower()
    return normalized if normalized in _AUDIENCE_ORDER else "community"


def _session_item(payload: dict[str, Any]) -> Optional[CalendarItemResponse]:
    starts_at = _parse_datetime(payload.get("starts_at"))
    if starts_at is None:
        return None
    session_type = str(payload.get("session_type") or "community").lower()
    access = payload.get("access") if isinstance(payload.get("access"), dict) else {}
    location_name = (
        payload.get("location_name")
        or payload.get("location_address")
        or payload.get("location")
    )
    return CalendarItemResponse(
        id=str(payload["id"]),
        source="session",
        audience=_session_audience(session_type),
        kind=session_type,
        title=str(payload.get("title") or "SwimBuddz session"),
        description=payload.get("description"),
        starts_at=starts_at,
        ends_at=_parse_datetime(payload.get("ends_at")),
        timezone=str(payload.get("timezone") or "Africa/Lagos"),
        location_name=str(location_name) if location_name else None,
        pool_id=str(payload["pool_id"]) if payload.get("pool_id") else None,
        status=str(payload.get("status") or "scheduled").lower(),
        href=f"/sessions/{payload['id']}/book",
        bookable=bool(access.get("bookable")),
    )


def _event_item(payload: dict[str, Any]) -> Optional[CalendarItemResponse]:
    starts_at = _parse_datetime(payload.get("start_time"))
    if starts_at is None:
        return None
    event_type = str(payload.get("event_type") or "event").lower()
    return CalendarItemResponse(
        id=str(payload["id"]),
        source="event",
        audience=_event_audience(payload.get("tier_access")),
        kind=event_type,
        title=str(payload.get("title") or "SwimBuddz event"),
        description=payload.get("description"),
        starts_at=starts_at,
        ends_at=_parse_datetime(payload.get("end_time")),
        timezone="Africa/Lagos",
        location_name=payload.get("location"),
        pool_id=str(payload["pool_id"]) if payload.get("pool_id") else None,
        status="scheduled",
        href=f"/community/events/{payload['id']}",
        bookable=False,
    )


@router.get("", response_model=CalendarResponse)
@router.get("/", response_model=CalendarResponse, include_in_schema=False)
async def get_calendar(
    request: Request,
    range_start: Optional[datetime] = Query(None, alias="from"),
    range_end: Optional[datetime] = Query(None, alias="to"),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
) -> CalendarResponse:
    """Return only calendar items visible to the current visitor or member."""
    now = utc_now()
    start = _aware(
        range_start
        or datetime(now.year, now.month, 1, tzinfo=now.tzinfo or timezone.utc)
    )
    end = _aware(range_end or (start + timedelta(days=395)))
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be later than `from`.",
        )
    if end - start > _MAX_RANGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Calendar ranges cannot exceed 400 days.",
        )

    is_admin = bool(current_user and is_admin_or_service(current_user))
    headers = _auth_headers(request)
    session_types = "community,club,cohort_class" if current_user else "community"

    tasks = [
        _fetch_optional(
            clients.sessions_client,
            f"/sessions/?types={session_types}",
            "Sessions",
            headers,
        ),
        _fetch_optional(
            clients.events_client,
            "/events/?upcoming_only=false",
            "Events",
            headers,
        ),
    ]
    if current_user and not is_admin:
        tasks.append(
            _fetch_optional(
                clients.members_client,
                "/members/me",
                "Membership",
                headers,
            )
        )

    results = await asyncio.gather(*tasks)
    sessions_data, sessions_error = results[0]
    events_data, events_error = results[1]
    profile_data: Any = None
    profile_error: Optional[str] = None
    if len(results) == 3:
        profile_data, profile_error = results[2]

    errors: dict[str, str] = {}
    if sessions_error:
        errors["sessions"] = sessions_error
    if events_error:
        errors["events"] = events_error
    if profile_error:
        errors["membership"] = profile_error

    allowed_event_audiences = (
        set(_AUDIENCE_ORDER)
        if is_admin
        else _member_audiences(profile_data)
        if current_user
        else {"community"}
    )

    items: list[CalendarItemResponse] = []
    for session in sessions_data or []:
        if not isinstance(session, dict) or "id" not in session:
            continue
        if not _in_range(session.get("starts_at"), start, end):
            continue
        session_type = str(session.get("session_type") or "").lower()
        if not current_user and session_type != "community":
            continue
        if current_user and not is_admin:
            access = session.get("access")
            if not isinstance(access, dict) or not access.get("visible"):
                continue
        item = _session_item(session)
        if item is not None:
            items.append(item)

    for event in events_data or []:
        if not isinstance(event, dict) or "id" not in event:
            continue
        if not _in_range(event.get("start_time"), start, end):
            continue
        audience = _event_audience(event.get("tier_access"))
        if audience not in allowed_event_audiences:
            continue
        item = _event_item(event)
        if item is not None:
            items.append(item)

    items.sort(key=lambda item: (item.starts_at, item.title.lower()))
    available = [
        audience
        for audience in _AUDIENCE_ORDER
        if any(item.audience == audience for item in items)
    ]
    return CalendarResponse(
        items=items,
        range_start=start,
        range_end=end,
        available_audiences=available,
        errors=errors,
    )
