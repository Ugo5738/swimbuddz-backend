"""Shared enrichment for every session-related member email."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.service_client import get_members_bulk, get_pod_by_id, internal_post
from services.communications_service.models import WeeklyDigestConfig
from services.communications_service.services.session_weather import (
    get_session_weather_summary,
    session_timezone,
)

logger = get_logger(__name__)

AUDIENCES = ("community", "club", "academy")

AUDIENCE_DEFAULTS = {
    "community": {
        "image_alt": "SwimBuddz Community members swimming together",
        "section_intro": "Social swims open to eligible Community members.",
        "gear_notes": "Swimsuit, goggles, cap, towel, and water.",
    },
    "club": {
        "image_alt": "SwimBuddz Club members practising in lanes",
        "section_intro": "Your pod practice and general Club sessions.",
        "gear_notes": (
            "Bring your usual swim kit and any training aids listed by your Pod Lead."
        ),
    },
    "academy": {
        "image_alt": "SwimBuddz Academy students in a coached lesson",
        "section_intro": "Your cohort lessons and this week's learning focus.",
        "gear_notes": (
            "Swimsuit, goggles, cap, towel, water, and any coach-assigned "
            "training aids."
        ),
    },
}


@dataclass
class SessionEmailContextBatch:
    """Presentation and operational facts loaded once for a set of sessions."""

    sessions: dict[str, dict]
    audience_configs: dict[str, dict]
    pods_by_session: dict[str, dict]
    pod_member_ids_by_session: dict[str, set[str]]


def session_audience(session_type: str | None) -> str:
    normalized = str(session_type or "").lower()
    if normalized == "cohort_class":
        return "academy"
    if normalized == "club":
        return "club"
    return "community"


def session_fee_amount(session: dict) -> float:
    """Return the internal kobo fee as a member-facing naira amount."""
    try:
        return float(session.get("pool_fee") or 0) / 100
    except (TypeError, ValueError):
        return 0


def _person_name(person: dict | None) -> str:
    if not person:
        return ""
    return (
        f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        or str(person.get("full_name") or "").strip()
    )


def _weather_text(weather: dict | None) -> str:
    if not weather:
        return ""
    summary = " | ".join(
        str(weather.get(key))
        for key in ("condition_text", "temperature_text", "rain_chance_text")
        if weather.get(key)
    )
    explanation = str(weather.get("explanation") or "").strip()
    if explanation:
        summary = f"{summary}. {explanation}".strip()
    return summary


def _transport_text(ride_configs: list[dict]) -> str:
    if not ride_configs:
        return ""

    areas = sorted(
        {
            str(config.get("ride_area_name"))
            for config in ride_configs
            if config.get("ride_area_name")
        }
    )
    pickup_names = sorted(
        {
            str(location.get("name"))
            for config in ride_configs
            for location in (config.get("pickup_locations") or [])
            if location.get("name")
        }
    )
    prices = [
        float(config.get("cost") or 0)
        for config in ride_configs
        if config.get("cost") is not None
    ]
    price = min(prices) if prices else 0

    parts = ["Transport available"]
    if areas:
        parts.append(f"from {', '.join(areas)}")
    if pickup_names:
        parts.append(f"via {', '.join(pickup_names)}")
    if price:
        parts.append(f"from NGN {price:,.0f}")
    return " ".join(parts)


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def _load_audience_configs(db: AsyncSession) -> dict[str, dict]:
    rows = (
        (
            await db.execute(
                select(WeeklyDigestConfig).order_by(WeeklyDigestConfig.audience)
            )
        )
        .scalars()
        .all()
    )
    rows_by_audience = {row.audience: row for row in rows}
    media_urls = await resolve_media_urls([row.featured_image_media_id for row in rows])
    frontend_url = get_settings().FRONTEND_URL.rstrip("/")

    configs: dict[str, dict] = {}
    for audience in AUDIENCES:
        row = rows_by_audience.get(audience)
        defaults = AUDIENCE_DEFAULTS[audience]
        media_id = row.featured_image_media_id if row else None
        configs[audience] = {
            "featured_image_url": media_urls.get(media_id)
            or f"{frontend_url}/email/digest/{audience}.webp",
            "image_alt": (
                row.image_alt if row and row.image_alt else defaults["image_alt"]
            ),
            "section_intro": (
                row.section_intro
                if row and row.section_intro
                else defaults["section_intro"]
            ),
            "default_gear_notes": (
                row.default_gear_notes
                if row and row.default_gear_notes
                else defaults["gear_notes"]
            ),
            "is_enabled": row.is_enabled if row else True,
        }
    return configs


async def build_session_email_contexts(
    db: AsyncSession,
    sessions: Sequence[dict],
    *,
    known_people: Sequence[dict] = (),
) -> SessionEmailContextBatch:
    """Load shared tier presentation and operational facts for session emails."""
    audience_configs = await _load_audience_configs(db)
    settings = get_settings()

    pods_by_session: dict[str, dict] = {}
    pod_member_ids_by_session: dict[str, set[str]] = {}
    for session in sessions:
        session_id = str(session["id"])
        if str(session.get("session_type") or "").lower() != "club" or not session.get(
            "pod_id"
        ):
            continue
        try:
            pod = await get_pod_by_id(
                str(session["pod_id"]),
                calling_service="communications",
            )
        except Exception as exc:
            logger.warning(
                "Session email pod lookup failed for session %s: %s",
                session_id,
                exc,
            )
            pod = None
        pod_member_ids_by_session[session_id] = {
            str(member_id) for member_id in ((pod or {}).get("active_member_ids") or [])
        }
        if pod:
            pods_by_session[session_id] = pod

    people_by_id = {
        str(person["id"]): person for person in known_people if person.get("id")
    }
    related_people_ids = {
        str(member_id)
        for session in sessions
        for member_id in (session.get("coach_member_ids") or [])
    }
    for pod in pods_by_session.values():
        related_people_ids.update(
            str(member_id)
            for member_id in (pod.get("pod_lead_id"), pod.get("assistant_pod_lead_id"))
            if member_id
        )
    missing_people_ids = related_people_ids - people_by_id.keys()
    if missing_people_ids:
        people = await get_members_bulk(
            sorted(missing_people_ids),
            calling_service="communications",
        )
        people_by_id.update(
            {str(person["id"]): person for person in people if person.get("id")}
        )

    session_ids = [str(session["id"]) for session in sessions]
    transport_by_session: dict[str, list[dict]] = {}
    if session_ids:
        try:
            response = await internal_post(
                service_url=settings.TRANSPORT_SERVICE_URL,
                path="/transport/sessions/ride-configs/batch",
                calling_service="communications",
                json={"session_ids": session_ids},
            )
            if response.status_code == 200:
                transport_by_session = response.json().get("configs", {})
            else:
                logger.warning(
                    "Session email transport lookup returned %s",
                    response.status_code,
                )
        except Exception as exc:
            logger.warning("Session email transport lookup failed: %s", exc)

    weather_results = await asyncio.gather(
        *(get_session_weather_summary(session) for session in sessions)
    )
    weather_by_session = {
        str(session["id"]): weather
        for session, weather in zip(sessions, weather_results)
    }

    contexts: dict[str, dict] = {}
    for session in sessions:
        session_id = str(session["id"])
        audience = session_audience(session.get("session_type"))
        config = audience_configs[audience]
        local_tz = session_timezone(session)
        local_start = datetime.fromisoformat(session["starts_at"]).astimezone(local_tz)
        local_end = datetime.fromisoformat(session["ends_at"]).astimezone(local_tz)

        pod = pods_by_session.get(session_id)
        scope_label = ""
        leaders: list[str] = []
        if audience == "club":
            if pod:
                pod_name = pod.get("handle") or pod.get("name") or "Pod"
                scope_label = f"{pod_name} Pod"
                lead_name = _person_name(people_by_id.get(str(pod.get("pod_lead_id"))))
                if lead_name:
                    leaders.append(f"Pod Lead: {lead_name}")
                assistant_name = _person_name(
                    people_by_id.get(str(pod.get("assistant_pod_lead_id")))
                )
                if assistant_name:
                    leaders.append(f"Assistant Pod Lead: {assistant_name}")
            else:
                scope_label = "General Club session"

        coach_names = [
            name
            for coach_id in (session.get("coach_member_ids") or [])
            if (name := _person_name(people_by_id.get(str(coach_id))))
        ]
        if coach_names:
            leaders.append(f"Coach: {', '.join(coach_names)}")

        weather_summary = weather_by_session.get(session_id)
        fee_amount = session_fee_amount(session)
        capacity = _safe_int(session.get("capacity"))
        occupied_slots = _safe_int(session.get("occupied_slots"))
        remaining_spots = max(0, capacity - occupied_slots)
        if capacity and remaining_spots == 0:
            availability_text = "Session is full"
        elif capacity:
            availability_text = f"{remaining_spots} spots left"
        else:
            availability_text = ""

        start_utc = datetime.fromisoformat(session["starts_at"]).astimezone(
            ZoneInfo("UTC")
        )
        end_utc = datetime.fromisoformat(session["ends_at"]).astimezone(ZoneInfo("UTC"))
        location = session.get("location_name") or session.get("location") or "TBD"
        calendar_params = urlencode(
            {
                "action": "TEMPLATE",
                "text": session["title"],
                "dates": (
                    f"{start_utc.strftime('%Y%m%dT%H%M%SZ')}/"
                    f"{end_utc.strftime('%Y%m%dT%H%M%SZ')}"
                ),
                "location": location,
            }
        )

        contexts[session_id] = {
            "id": session_id,
            "title": session["title"],
            "session_type": str(session.get("session_type") or ""),
            "audience": audience,
            "featured_image_url": config["featured_image_url"],
            "image_alt": config["image_alt"],
            "section_intro": config["section_intro"],
            "gear_notes": config["default_gear_notes"],
            "audience_enabled": config["is_enabled"],
            "date": local_start.strftime("%A, %B %d, %Y"),
            "digest_date": local_start.strftime("%A, %B %d"),
            "time": local_start.strftime("%I:%M %p"),
            "time_range": (
                f"{local_start.strftime('%I:%M %p')} - "
                f"{local_end.strftime('%I:%M %p')}"
            ),
            "location": location,
            "address": session.get("location_address") or "",
            "scope_label": scope_label,
            "leader_label": " | ".join(leaders),
            "purpose": session.get("lesson_title") or session.get("description"),
            "weather_summary": weather_summary,
            "weather_text": _weather_text(weather_summary),
            "transport_text": _transport_text(
                transport_by_session.get(session_id) or []
            ),
            "pool_fee": fee_amount,
            "fee_text": (
                f"NGN {fee_amount:,.0f}" if fee_amount else "No additional pool fee"
            ),
            "capacity": capacity,
            "occupied_slots": occupied_slots,
            "remaining_spots": remaining_spots,
            "availability_text": availability_text,
            "calendar_url": (
                "https://calendar.google.com/calendar/render?" f"{calendar_params}"
            ),
        }

    return SessionEmailContextBatch(
        sessions=contexts,
        audience_configs=audience_configs,
        pods_by_session=pods_by_session,
        pod_member_ids_by_session=pod_member_ids_by_session,
    )


def with_booking_state(
    context: dict,
    *,
    is_booked: bool,
    action_url: str | None = None,
) -> dict:
    """Add recipient-specific booking state to a shared session context."""
    enriched = dict(context)
    enriched.update(
        {
            "is_booked": is_booked,
            "state_label": (
                "You are booked" if is_booked else "Available for you to book"
            ),
            "action_label": "Manage booking" if is_booked else "Book session",
        }
    )
    if action_url:
        enriched["action_url"] = action_url
    return enriched
