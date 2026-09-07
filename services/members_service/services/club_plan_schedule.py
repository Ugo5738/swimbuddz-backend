"""Actual-session Club pricing. No calendar estimates or global session rate."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from services.members_service.models import ClubPlanSession


async def fetch_schedule(**query) -> list[dict]:
    try:
        response = await internal_post(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path="/internal/sessions/club-schedule/query",
            calling_service="members",
            json=query,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Club schedule is temporarily unavailable") from exc
    if response.status_code >= 400:
        raise HTTPException(503, "Could not validate the actual Club schedule")
    return response.json()


async def hydrate_schedules(plans) -> None:
    plans = [plan for plan in plans if plan is not None]
    ids = {
        str(link.session_id)
        for plan in plans
        for link in getattr(plan, "session_links", [])
    }
    rows = []
    ordered_ids = sorted(ids)
    for offset in range(0, len(ordered_ids), 260):
        rows.extend(
            await fetch_schedule(session_ids=ordered_ids[offset : offset + 260])
        )
    by_id = {row["id"]: row for row in rows}
    for plan in plans:
        plan._actual_session_rows = by_id


def remaining_links(plan, *, on_date: date | None = None):
    at = (
        datetime.combine(on_date, time.min, tzinfo=ZoneInfo("Africa/Lagos"))
        if on_date
        else utc_now()
    )
    live = getattr(plan, "_actual_session_rows", {})
    result = []
    for link in getattr(plan, "session_links", []):
        row = live.get(str(link.session_id))
        if not row or row["status"] != "scheduled" or row["session_type"] != "club":
            continue
        starts = datetime.fromisoformat(row["starts_at"])
        local_day = starts.astimezone(
            ZoneInfo(row.get("timezone") or "Africa/Lagos")
        ).date()
        if (
            starts >= at
            and plan.period_start <= local_day <= plan.period_end
            and row["pool_id"] == str(link.pool_id)
        ):
            result.append(link)
    return result


def actual_plan_price(plan, *, on_date: date | None = None):
    links = getattr(plan, "session_links", [])
    if not links:
        return (
            0,
            0,
            False,
            "Admin must link and publish this quarter's actual Club sessions before it can be purchased",
        )
    remaining = remaining_links(plan, on_date=on_date)
    if len(remaining) < plan.minimum_entry_sessions:
        return (
            0,
            len(remaining),
            False,
            f"Club entry closes below {plan.minimum_entry_sessions} remaining sessions; use Community drop-ins until the next quarter",
        )
    total_weight = sum(link.fee_kobo for link in links)
    remaining_weight = sum(link.fee_kobo for link in remaining)
    if total_weight == 0:
        total_weight, remaining_weight = len(links), len(remaining)
    amount = (plan.club_fee_kobo * remaining_weight + total_weight - 1) // total_weight
    return amount, len(remaining), True, None


def snapshot_session(row: dict) -> ClubPlanSession:
    import uuid

    return ClubPlanSession(
        session_id=uuid.UUID(row["id"]),
        pool_id=uuid.UUID(row["pool_id"]),
        title=row["title"],
        starts_at=datetime.fromisoformat(row["starts_at"]),
        ends_at=datetime.fromisoformat(row["ends_at"]),
        fee_kobo=int(row["fee_kobo"]),
        pricing_snapshot=row.get("pricing") or {},
    )


async def selected_session_snapshots(body, club, db):
    from sqlalchemy import select
    from services.members_service.models import Pod

    ids = [str(value) for value in body.session_ids]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "A session can only be included once")
    rows = await fetch_schedule(session_ids=ids) if ids else []
    if len(rows) != len(ids):
        raise HTTPException(422, "One or more selected sessions no longer exist")
    pod_ids = {row["pod_id"] for row in rows if row.get("pod_id")}
    valid_pods = (
        set(
            str(value)
            for value in (
                await db.execute(select(Pod.id).where(Pod.club_id == club.id))
            ).scalars()
        )
        if pod_ids
        else set()
    )
    for row in rows:
        starts = datetime.fromisoformat(row["starts_at"])
        day = starts.astimezone(ZoneInfo(row.get("timezone") or "Africa/Lagos")).date()
        if (
            row["session_type"] != "club"
            or row["status"] not in {"scheduled", "draft"}
            or not row.get("pool_id")
            or not body.period_start <= day <= body.period_end
            or (row.get("pod_id") and row["pod_id"] not in valid_pods)
        ):
            raise HTTPException(
                422,
                "Choose scheduled Club sessions within this quarter and Club location's pods",
            )
    return [snapshot_session(row) for row in rows]
