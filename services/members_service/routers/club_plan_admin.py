"""Reviewable quarterly drafts; publication is always an explicit Admin action."""

import calendar
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubPlanVersion,
    CommunityExperienceOffering,
)
from services.members_service.schemas import ClubPlanCreate, ClubPlanResponse
from services.members_service.routers._club_pricing import plan_response
from services.members_service.services.club_plan_schedule import (
    fetch_schedule,
    hydrate_schedules,
    selected_session_snapshots,
)

router = APIRouter(
    prefix="/clubs/admin/plans",
    tags=["club-plan-drafts"],
    dependencies=[Depends(require_admin)],
)


def next_quarter(period_end: date) -> tuple[date, date]:
    start = date(
        period_end.year + (period_end.month == 12),
        (period_end.month // 3 * 3) % 12 + 1,
        1,
    )
    end_month = start.month + 2
    return start, date(
        start.year, end_month, calendar.monthrange(start.year, end_month)[1]
    )


def suggested_experience_day(period_end: date) -> date:
    if period_end.month == 12:
        first = date(period_end.year, 12, 1)
        return first + timedelta(days=(5 - first.weekday()) % 7)
    return period_end - timedelta(days=(period_end.weekday() - 5) % 7)


async def _plan(db, plan_id, *, draft=False):
    plan = (
        await db.execute(
            select(ClubPlanVersion)
            .where(ClubPlanVersion.id == plan_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, "Club plan not found")
    if draft and plan.published_at:
        raise HTTPException(
            409, "Published commercial terms are immutable; create a new draft"
        )
    return plan


@router.get("/{plan_id}/schedule")
async def plan_schedule(
    plan_id: uuid.UUID,
    pool_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    plan = await db.get(ClubPlanVersion, plan_id)
    if plan is None:
        raise HTTPException(404, "Club plan not found")
    rows = await fetch_schedule(
        pool_id=str(pool_id or plan.pool_id),
        period_start=plan.period_start.isoformat(),
        period_end=plan.period_end.isoformat(),
    )
    selected_ids = {str(link.session_id) for link in plan.session_links}
    # Include explicitly selected alternate venues in the review as well.
    missing = selected_ids - {row["id"] for row in rows}
    if missing:
        rows += await fetch_schedule(session_ids=sorted(missing))
    snapshots = {str(link.session_id): link for link in plan.session_links}
    rows = [
        {
            **row,
            "included_fee_kobo": snapshots[row["id"]].fee_kobo
            if row["id"] in snapshots
            else None,
        }
        for row in rows
    ]
    return {
        "sessions": rows,
        "selected_session_ids": sorted(selected_ids),
        "recommended_fee_kobo": sum(
            row["fee_kobo"]
            for row in rows
            if row["id"] in selected_ids and row["status"] in {"scheduled", "draft"}
        ),
        "suggested_experience_date": suggested_experience_day(plan.period_end),
    }


@router.put("/{plan_id}", response_model=ClubPlanResponse)
async def update_draft(
    plan_id: uuid.UUID, body: ClubPlanCreate, db: AsyncSession = Depends(get_async_db)
):
    plan = await _plan(db, plan_id, draft=True)
    club = await db.get(Club, plan.club_id)
    if body.is_active:
        raise HTTPException(422, "Use Publish after reviewing the draft")
    links = await selected_session_snapshots(body, club, db)
    if body.currency != "NGN":
        raise HTTPException(422, "Session-derived Club plans currently require NGN")
    values = body.model_dump(exclude={"session_ids", "sessions_included"})
    offering = (
        await db.get(CommunityExperienceOffering, body.community_experience_offering_id)
        if body.community_experience_offering_id
        else None
    )
    if body.community_experience_offering_id and (
        not offering
        or not offering.is_active
        or offering.currency != body.currency
        or offering.period_start != body.period_start
        or offering.period_end != body.period_end
    ):
        raise HTTPException(
            422, "Link an active Experience in the same quarter and currency"
        )
    values["community_experience_fee_kobo"] = (
        offering.club_bundle_fee_kobo if offering else 0
    )
    values["community_experience_default_selected"] = bool(
        offering and body.community_experience_default_selected
    )
    recommended = sum(link.fee_kobo for link in links)
    values["club_fee_kobo"] = (
        body.club_fee_kobo if body.club_fee_kobo is not None else recommended
    )
    for key, value in values.items():
        setattr(plan, key, value)
    # Delete-orphan links before inserting the same session identities again.
    plan.session_links.clear()
    await db.flush()
    plan.session_links = links
    plan.sessions_included, plan.recommended_fee_kobo = len(links), recommended
    await db.commit()
    await hydrate_schedules([plan])
    return plan_response(plan, club)


@router.post("/{plan_id}/publish", response_model=ClubPlanResponse)
async def publish_draft(plan_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    plan = await _plan(db, plan_id)
    club = await db.get(Club, plan.club_id)
    if plan.published_at:
        await hydrate_schedules([plan])
        return plan_response(plan, club)
    if not club or not club.is_active:
        raise HTTPException(409, "This Club location is inactive")
    await hydrate_schedules([plan])
    live = plan._actual_session_rows
    if not plan.session_links or any(
        str(link.session_id) not in live
        or live[str(link.session_id)]["status"] not in {"scheduled", "draft"}
        or datetime.fromisoformat(live[str(link.session_id)]["starts_at"])
        != link.starts_at
        or int(live[str(link.session_id)]["fee_kobo"]) != link.fee_kobo
        for link in plan.session_links
    ):
        raise HTTPException(
            409, "Schedule or prices changed; review and save the draft again"
        )
    if plan.sessions_included < plan.minimum_entry_sessions:
        raise HTTPException(
            422, "The quarter has fewer sessions than the new-entry minimum"
        )
    response = await internal_post(
        service_url=get_settings().SESSIONS_SERVICE_URL,
        path="/internal/sessions/club-schedule/publish",
        calling_service="members",
        json={
            "sessions": [
                {
                    "id": str(link.session_id),
                    "fee_kobo": link.fee_kobo,
                    "starts_at": link.starts_at.isoformat(),
                    "pool_id": str(link.pool_id),
                }
                for link in plan.session_links
            ]
        },
    )
    if response.status_code >= 400:
        raise HTTPException(
            409, "The Session schedule changed; refresh and review before publishing"
        )
    plan.is_active, plan.published_at = True, utc_now()
    await db.commit()
    await hydrate_schedules([plan])
    return plan_response(plan, club)


class NextQuarterRequest(BaseModel):
    generate_sessions: bool = False
    expected_staff: int = Field(default=0, ge=0, le=50)
    lanes: int = Field(default=1, ge=1, le=50)


@router.post("/{plan_id}/next-quarter", response_model=ClubPlanResponse)
async def generate_next_quarter(
    plan_id: uuid.UUID,
    body: NextQuarterRequest,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    source = await _plan(db, plan_id)
    club = await db.get(Club, source.club_id)
    start, end = next_quarter(source.period_end)
    existing = (
        await db.execute(
            select(ClubPlanVersion).where(
                ClubPlanVersion.source_plan_id == source.id,
                ClubPlanVersion.period_start == start,
            )
        )
    ).scalar_one_or_none()
    if existing:
        await hydrate_schedules([existing])
        return plan_response(existing, club)
    rows = await fetch_schedule(
        pool_id=str(club.default_pool_id),
        period_start=start.isoformat(),
        period_end=end.isoformat(),
    )
    if body.generate_sessions:
        if not source.session_links:
            raise HTTPException(
                422, "Link a source Club session before generating a recurring schedule"
            )
        from services.members_service.routers._club_pricing import _WEEKDAY_NUMBER

        response = await internal_post(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path="/internal/sessions/club-schedule/generate",
            calling_service="members",
            json={
                "club_id": str(club.id),
                "source_session_id": str(source.session_links[0].session_id),
                "pool_id": str(club.default_pool_id),
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "weekday": _WEEKDAY_NUMBER[
                    str(
                        getattr(
                            club.default_session_day, "value", club.default_session_day
                        )
                    )
                ],
                "starts_at_local": club.default_session_time.isoformat(),
                "duration_minutes": club.default_session_duration_minutes,
                "expected_staff": body.expected_staff,
                "lanes": body.lanes,
            },
        )
        if response.status_code >= 400:
            raise HTTPException(
                503, "Could not generate the next schedule; nothing was published"
            )
        rows = response.json()
    # Never clone a previous quarter's Experience or commercial override.
    from services.members_service.routers.clubs import _validate_club_pool_area

    await _validate_club_pool_area(
        operating_area_id=club.operating_area_id, default_pool_id=club.default_pool_id
    )
    draft_body = ClubPlanCreate(
        name=f"{start.year} Q{(start.month-1)//3+1} Club — {club.name}",
        period_start=start,
        period_end=end,
        effective_from=date.today(),
        is_active=False,
        session_ids=[
            row["id"] for row in rows if row["status"] in {"scheduled", "draft"}
        ],
        minimum_entry_sessions=source.minimum_entry_sessions,
        capacity=source.capacity,
        refreshments_included=source.refreshments_included,
        premium_venue_note=source.premium_venue_note,
        community_experience_default_selected=False,
        currency=source.currency,
    )
    links = await selected_session_snapshots(draft_body, club, db)
    values = draft_body.model_dump(
        exclude={"session_ids", "sessions_included", "club_fee_kobo"}
    )
    values.update(
        community_experience_fee_kobo=0, community_experience_default_selected=False
    )
    recommended = sum(link.fee_kobo for link in links)
    draft = ClubPlanVersion(
        club_id=club.id,
        pool_id=club.default_pool_id,
        operating_area_id=club.operating_area_id,
        source_plan_id=source.id,
        sessions_included=len(links),
        recommended_fee_kobo=recommended,
        club_fee_kobo=recommended,
        published_at=None,
        session_links=links,
        **values,
    )
    db.add(draft)
    await db.commit()
    await hydrate_schedules([draft])
    return plan_response(draft, club)
