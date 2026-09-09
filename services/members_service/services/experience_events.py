"""Live Event validation and redacted presentation for Community packages."""

from datetime import datetime
import httpx
from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post


async def event_request(action: str, payload: dict) -> list[dict]:
    try:
        response = await internal_post(
            service_url=get_settings().EVENTS_SERVICE_URL,
            path=f"/internal/events/experiences/{action}",
            calling_service="members",
            json=payload,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            503, "Experience Event details are temporarily unavailable"
        ) from exc
    if response.status_code >= 400:
        detail = response.json().get("detail", "Could not validate Experience Events")
        raise HTTPException(response.status_code, detail)
    return response.json()


async def live_events(offering, *, for_sale=False):
    ids = [str(link.event_id) for link in offering.event_links]
    if not ids:
        if for_sale:
            raise HTTPException(
                409,
                "Admin must link the actual Experience Event before ticket sales open",
            )
        return []
    rows = await event_request("query", {"event_ids": ids})
    by_id = {row["id"]: row for row in rows}
    if for_sale:
        if len(rows) != len(ids) or any(
            row["status"] != "published" or row.get("offering_id") != str(offering.id)
            for row in rows
        ):
            raise HTTPException(409, "One or more Experience Events are not available")
        if any(datetime.fromisoformat(row["start_time"]) <= utc_now() for row in rows):
            raise HTTPException(
                409, "This Experience has already started; ticket sales are closed"
            )
    return [by_id[event_id] for event_id in ids if event_id in by_id]


def effective_capacity(offering, events):
    limits = [
        limit
        for limit in [offering.capacity] + [row.get("max_capacity") for row in events]
        if limit is not None
    ]
    return min(limits) if limits else None


def public_event(row: dict, *, reveal_private=False):
    return {
        key: (
            None
            if key == "location"
            and row.get("is_location_private")
            and not reveal_private
            else row.get(key)
        )
        for key in (
            "id",
            "title",
            "description",
            "start_time",
            "end_time",
            "timezone",
            "location",
            "location_area",
            "status",
            "is_location_private",
        )
    }
