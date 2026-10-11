"""Quarterly product metrics through domain-owned HTTP contracts only."""

import asyncio
import json
from pathlib import Path

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.reporting import ReportingSection
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from services.reporting_service.services.locations import canonical_location
from services.reporting_service.services.quarter_utils import quarter_date_range


logger = get_logger(__name__)


def quarter_editorial(year: int, quarter: int) -> dict:
    path = Path(__file__).parents[1] / "assets" / "quarterly_highlights.json"
    return json.loads(path.read_text()).get(f"{year}-Q{quarter}", {})


async def fetch_section(url: str, path: str, params: dict) -> dict:
    try:
        response = await internal_get(
            service_url=url,
            path=path,
            calling_service="reporting",
            params=params,
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(
            payload.get("metrics"), dict
        ):
            raise ValueError("Invalid quarterly source contract")
        return ReportingSection.model_validate(payload).model_dump()
    except Exception as exc:
        logger.warning(
            "Extended quarterly source %s failed (%s)", path, type(exc).__name__
        )
        return ReportingSection(
            available=False,
            notes=["Source unavailable; regenerate after the service is restored."],
        ).model_dump()


def location_metrics(sections: dict) -> list[dict]:
    locations, member_ids = {}, {}
    session_source = sections.get("sessions") or {}
    member_source = sections.get("members") or {}
    sessions_available = bool(
        session_source.get("available", bool(session_source.get("details")))
    )
    members_available = bool(
        member_source.get("available", bool(member_source.get("details")))
    )
    attendance_available = sessions_available and (
        session_source.get("details") or {}
    ).get("attendance_available", False)

    def row_for(name):
        key, label = canonical_location(name)
        row = locations.setdefault(
            key,
            {
                "location": label,
                "calendar_sessions": 0 if sessions_available else None,
                "completed_sessions": 0 if sessions_available else None,
                "checkins": 0 if attendance_available else None,
                "sessions_with_checkins": 0 if attendance_available else None,
                "member_swimmer_hours": 0.0 if attendance_available else None,
                "pool_ids": [],
                "clubs_configured": 0 if members_available else None,
                "pods_configured": 0 if members_available else None,
                "pods_populated_at_end": 0 if members_available else None,
            },
        )
        return key, row

    for session in (session_source.get("details") or {}).get("sessions", []):
        key, row = row_for(session.get("location"))
        row["calendar_sessions"] += 1
        row["completed_sessions"] += session.get("status") == "completed"
        if attendance_available:
            row["checkins"] += int(session.get("attendance") or 0)
            row["sessions_with_checkins"] += int(session.get("attendance") or 0) > 0
            row["member_swimmer_hours"] = round(
                row["member_swimmer_hours"]
                + float(session.get("hours") or 0)
                * int(session.get("member_attendance") or 0),
                2,
            )
        member_ids.setdefault(key, set()).update(session.get("member_ids") or [])
        if session.get("pool_id") and session["pool_id"] not in row["pool_ids"]:
            row["pool_ids"].append(session["pool_id"])
    for club in (member_source.get("details") or {}).get("clubs", []):
        key, row = row_for(club.get("location") or club.get("name"))
        row["clubs_configured"] += 1
        row["pods_configured"] += club.get("pods_configured", 0)
        row["pods_populated_at_end"] += club.get("pods_populated_at_end", 0)
        if club.get("pool_id") and club["pool_id"] not in row["pool_ids"]:
            row["pool_ids"].append(club["pool_id"])
    for key, row in locations.items():
        row["unique_members"] = (
            len(member_ids.get(key, set())) if attendance_available else None
        )
    return list(locations.values())


def strip_internal_details(sections: dict) -> dict:
    """Community snapshots must not leak pod rosters or attendee identifiers."""
    for key in ("members", "sessions"):
        if key in sections:
            section = sections[key]
            section["details"] = {}  # Detailed joins belong in the admin review.
    return sections


async def fetch_ecosystem_stats(year: int, quarter: int) -> dict:
    settings = get_settings()
    start, end = quarter_date_range(year, quarter)
    params = {"from": start.isoformat(), "to": end.isoformat()}
    sources = {
        "members": (
            settings.MEMBERS_SERVICE_URL,
            "/internal/members/reporting-summary",
        ),
        "sessions": (
            settings.SESSIONS_SERVICE_URL,
            "/internal/sessions/reporting-summary",
        ),
        "wallet": (settings.WALLET_SERVICE_URL, "/internal/wallet/reporting-summary"),
        "media": (settings.MEDIA_SERVICE_URL, "/internal/media/reporting-summary"),
        "content": (
            settings.COMMUNICATIONS_SERVICE_URL,
            "/internal/communications/reporting-summary",
        ),
        "strokelab": (settings.AI_SERVICE_URL, "/internal/ai/reporting-summary"),
        "academy": (
            settings.ACADEMY_SERVICE_URL,
            "/internal/academy/reporting-summary",
        ),
    }
    results = await asyncio.gather(
        *(fetch_section(url, path, params) for url, path in sources.values())
    )
    raw_sections = dict(zip(sources, results))
    locations = location_metrics(raw_sections)
    session_section = raw_sections["sessions"]
    if session_section.get("available"):
        session_section["metrics"]["locations_with_calendar_sessions"] = sum(
            (r.get("calendar_sessions") or 0) > 0 for r in locations
        )
        session_section["metrics"]["locations_with_recorded_swims"] = (
            sum((r.get("checkins") or 0) > 0 for r in locations)
            if session_section.get("details", {}).get("attendance_available")
            else None
        )
    sections = strip_internal_details(raw_sections)
    return {
        "version": 1,
        "computed_at": utc_now().isoformat(),
        "sections": sections,
        "locations": locations,
        "highlights": quarter_editorial(year, quarter).get("highlights", []),
        "external_podcast_episodes": quarter_editorial(year, quarter).get(
            "external_podcast_episodes", []
        ),
    }


async def fetch_member_extensions(auth_id: str, year: int, quarter: int) -> dict:
    settings = get_settings()
    start, end = quarter_date_range(year, quarter)
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "member_auth_id": auth_id,
    }
    members, wallet = await asyncio.gather(
        fetch_section(
            settings.MEMBERS_SERVICE_URL, "/internal/members/reporting-summary", params
        ),
        fetch_section(
            settings.WALLET_SERVICE_URL, "/internal/wallet/reporting-summary", params
        ),
    )
    return {"members": members, "wallet": wallet}
