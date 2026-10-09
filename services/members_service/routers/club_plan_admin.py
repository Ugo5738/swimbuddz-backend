"""Reviewable quarterly drafts; publication is always an explicit Admin action."""

import calendar
import uuid
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubEnrollment,
    ClubPlanVersion,
    CommunityExperienceOffering,
    Member,
)
from services.members_service.schemas import ClubPlanCreate, ClubPlanResponse
from services.members_service.schemas.club_merchandising import (
    AttachClubExperienceRequest,
)
from services.members_service.routers._club_pricing import plan_response
from services.members_service.services.club_plan_schedule import (
    fetch_schedule,
    hydrate_schedules,
    selected_session_snapshots,
)

logger = get_logger(__name__)

# Pricing a full quarter takes longer than the ordinary 10-second internal call.
# Leave time for validation and saving the draft within the gateway's 60 seconds.
_QUARTER_GENERATION_TIMEOUT = 45.0

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


@router.post("/{plan_id}/sync-prepaid-reservations")
async def sync_prepaid_reservations(
    plan_id: uuid.UUID,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """Repair historical purchases and interrupted fulfillment, safely on replay."""
    from services.members_service.services.club_reservations import (
        reserve_enrollment_swims,
    )

    plan = await db.get(ClubPlanVersion, plan_id)
    if not plan or not plan.published_at or not plan.session_links:
        raise HTTPException(409, "Choose a published Club quarter")
    enrollments = list(
        (
            await db.execute(
                select(ClubEnrollment)
                .where(
                    ClubEnrollment.plan_version_id == plan_id,
                    ClubEnrollment.payment_mode == "quarterly_prepaid",
                    ClubEnrollment.status == "active",
                    ClubEnrollment.ends_at > utc_now(),
                )
                .order_by(ClubEnrollment.id)
            )
        ).scalars()
    )
    if dry_run:
        return {
            "dry_run": True,
            "candidate_enrollment_ids": [str(row.id) for row in enrollments],
            "included_session_ids": [
                str(link.session_id) for link in plan.session_links
            ],
            "synced_enrollment_ids": [],
            "failed_enrollment_ids": [],
        }
    completed, failures = [], []
    for enrollment in enrollments:
        member = await db.get(Member, enrollment.member_id)
        try:
            if member is None:
                raise HTTPException(409, "Enrollment member is missing")
            await reserve_enrollment_swims(enrollment, plan, member)
            completed.append(str(enrollment.id))
        except Exception:
            logger.exception(
                "Prepaid reservation sync failed for enrollment %s", enrollment.id
            )
            # Keep the batch retryable and expose affected IDs to an Admin.
            failures.append(str(enrollment.id))
    return {"synced_enrollment_ids": completed, "failed_enrollment_ids": failures}


@router.put("/{plan_id}/community-experience", response_model=ClubPlanResponse)
async def attach_plan_experience(
    plan_id: uuid.UUID,
    body: AttachClubExperienceRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Attach a newly available optional offering, including after publication.

    Existing links cannot be replaced: pending checkouts and paid fulfillments
    may rely on that identity. Adding a previously absent offer never selects it
    on an application, changes Club prices/schedules, or touches a payment.
    """
    plan = await _plan(db, plan_id)
    club = await db.get(Club, plan.club_id)
    if plan.community_experience_offering_id:
        if plan.community_experience_offering_id != body.offering_id:
            raise HTTPException(
                409,
                "An existing Experience link cannot be replaced; create a new plan draft",
            )
        await hydrate_schedules([plan])
        return plan_response(plan, club)
    offering = await db.get(CommunityExperienceOffering, body.offering_id)
    if (
        not offering
        or not offering.is_active
        or offering.currency != plan.currency
        or offering.period_start != plan.period_start
        or offering.period_end != plan.period_end
    ):
        raise HTTPException(
            422, "Link an active Experience in the same quarter and currency"
        )
    from services.members_service.services.experience_events import live_events

    await live_events(offering, for_sale=True)
    plan.community_experience_offering_id = offering.id
    plan.community_experience_fee_kobo = offering.club_bundle_fee_kobo
    plan.community_experience_default_selected = False
    await db.commit()
    await hydrate_schedules([plan])
    return plan_response(plan, club)


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
        if row.get("club_id") in (None, str(plan.club_id))
        and row.get("club_access_mode", "plan_included") == "plan_included"
    ]
    return {
        "warnings": [
            "Refreshments are promised, but some selected Sessions have no refreshment cost line. Review inherited operating rates or confirm they are free/sponsored."
        ]
        if plan.refreshments_included
        and any(
            row["id"] in selected_ids
            and not any(
                "refreshment" in line.get("category", "").lower()
                for line in row.get("pricing", {}).get("cost_lines", [])
            )
            for row in rows
        )
        else [],
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
        or datetime.fromisoformat(live[str(link.session_id)]["ends_at"]) != link.ends_at
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
            "club_id": str(plan.club_id),
            "sessions": [
                {
                    "id": str(link.session_id),
                    "fee_kobo": link.fee_kobo,
                    "starts_at": link.starts_at.isoformat(),
                    "ends_at": link.ends_at.isoformat(),
                    "pool_id": str(link.pool_id),
                }
                for link in plan.session_links
            ],
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


class TemplateSelection(BaseModel):
    template_id: uuid.UUID | None = None
    template_ids: list[uuid.UUID] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def valid_templates(self):
        if self.template_id and self.template_ids:
            raise ValueError("Choose template_id or template_ids, not both")
        if len(set(self.template_ids)) != len(self.template_ids):
            raise ValueError("Template IDs must be unique")
        return self


class QuarterRecommendationRequest(TemplateSelection):
    club_id: uuid.UUID
    year: int = Field(ge=2026, le=2100)
    quarter: int = Field(ge=1, le=4)
    pricing_settings: dict | None = None
    excluded_dates: list[date] = Field(default_factory=list, max_length=52)
    capacity: int = Field(default=20, ge=1, le=500)
    minimum_entry_sessions: int = Field(default=5, ge=1, le=52)
    refreshments_included: bool = True


async def recommend_quarter(body, db, *, source=None):
    from services.members_service.routers.clubs import _validate_club_pool_area
    from services.members_service.routers._club_pricing import _WEEKDAY_NUMBER

    club = (
        await db.execute(select(Club).where(Club.id == body.club_id).with_for_update())
    ).scalar_one_or_none()
    if not club or not club.is_active or not club.default_pool_id:
        raise HTTPException(
            422, "Configure an active Club location and home pool first"
        )
    await _validate_club_pool_area(
        operating_area_id=club.operating_area_id, default_pool_id=club.default_pool_id
    )
    month = 3 * (body.quarter - 1) + 1
    start = date(body.year, month, 1)
    end = date(body.year, month + 2, calendar.monthrange(body.year, month + 2)[1])
    existing = (
        (
            await db.execute(
                select(ClubPlanVersion)
                .where(
                    ClubPlanVersion.club_id == club.id,
                    ClubPlanVersion.period_start == start,
                    ClubPlanVersion.period_end == end,
                )
                .order_by(ClubPlanVersion.created_at)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if existing and existing.published_at:
        await hydrate_schedules([existing])
        return plan_response(existing, club)
    if existing and existing.session_links:
        raise HTTPException(
            409,
            "A draft already exists for this Club and quarter. Open the existing draft to edit it.",
        )
    if existing and existing.currency != "NGN":
        raise HTTPException(422, "Session-derived Club plans currently require NGN")
    # Omitted settings should not erase an empty draft's existing Admin choices.
    draft_settings = {
        key: getattr(existing, key)
        if existing and key not in body.model_fields_set
        else getattr(body, key)
        for key in ("capacity", "minimum_entry_sessions", "refreshments_included")
    }
    try:
        response = await internal_post(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path="/internal/sessions/club-schedule/generate",
            calling_service="members",
            timeout=_QUARTER_GENERATION_TIMEOUT,
            json={
                "club_id": str(club.id),
                "pool_id": str(club.default_pool_id),
                "template_id": str(body.template_id) if body.template_id else None,
                "template_ids": [str(value) for value in body.template_ids],
                "title": f"{club.name} Club practice",
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "weekday": _WEEKDAY_NUMBER[
                    getattr(club.default_session_day, "value", club.default_session_day)
                ],
                "starts_at_local": club.default_session_time.isoformat(),
                "duration_minutes": club.default_session_duration_minutes,
                "capacity": draft_settings["capacity"] or body.capacity,
                "pricing_settings": body.pricing_settings,
                "excluded_dates": [day.isoformat() for day in body.excluded_dates],
            },
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            "Club quarter generation is taking longer than expected. Nothing was "
            "published. Retry to recover the draft using the same sessions.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            503,
            "Club schedule is temporarily unavailable. Retry generating the quarter.",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            response.json().get(
                "detail", "Could not generate the quarter; nothing was published"
            ),
        )
    rows = response.json()
    draft_body = ClubPlanCreate(
        name=f"{start.year} Q{body.quarter} Club — {club.name}",
        period_start=start,
        period_end=end,
        effective_from=date.today(),
        is_active=False,
        session_ids=[
            row["id"] for row in rows if row["status"] in {"scheduled", "draft"}
        ],
        **draft_settings,
        community_experience_default_selected=False,
        currency="NGN",
    )
    links = await selected_session_snapshots(draft_body, club, db)
    values = draft_body.model_dump(
        exclude={"session_ids", "sessions_included", "club_fee_kobo"}
    )
    values.update(
        community_experience_fee_kobo=0, community_experience_default_selected=False
    )
    recommended = sum(link.fee_kobo for link in links)
    if existing:
        # Fill this same empty draft, preserving names, sale dates, Experience
        # choices and any deliberate final-price override. Only the generated
        # schedule/economics and explicitly supplied settings are populated.
        if existing.club_fee_kobo == existing.recommended_fee_kobo:
            existing.club_fee_kobo = recommended
        existing.session_links = links
        existing.sessions_included = len(links)
        existing.recommended_fee_kobo = recommended
        existing.source_template_id = (
            body.template_id
            or (body.template_ids[0] if body.template_ids else None)
            or uuid.uuid5(club.id, "primary-club-template")
        )
        existing.pool_id = club.default_pool_id
        existing.operating_area_id = club.operating_area_id
        existing.is_active = False
        for key, value in draft_settings.items():
            setattr(existing, key, value)
        await db.commit()
        await hydrate_schedules([existing])
        return plan_response(existing, club)
    draft = ClubPlanVersion(
        club_id=club.id,
        pool_id=club.default_pool_id,
        operating_area_id=club.operating_area_id,
        source_plan_id=source.id if source else None,
        source_template_id=body.template_id
        or (body.template_ids[0] if body.template_ids else None)
        or uuid.uuid5(club.id, "primary-club-template"),
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


@router.post("/recommendations", response_model=ClubPlanResponse)
async def create_recommendation(
    body: QuarterRecommendationRequest, db: AsyncSession = Depends(get_async_db)
):
    return await recommend_quarter(body, db)


class NextQuarterRequest(TemplateSelection):
    pricing_settings: dict | None = None
    excluded_dates: list[date] = Field(default_factory=list, max_length=52)


@router.post("/{plan_id}/next-quarter", response_model=ClubPlanResponse)
async def generate_next_quarter(
    plan_id: uuid.UUID,
    body: NextQuarterRequest,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    source = await _plan(db, plan_id)
    start, _ = next_quarter(source.period_end)
    template_ids = body.template_ids
    if not body.template_id and not template_ids and source.session_links:
        rows = await fetch_schedule(
            session_ids=[str(link.session_id) for link in source.session_links]
        )
        template_ids = sorted(
            {uuid.UUID(row["template_id"]) for row in rows if row.get("template_id")}
        )
    return await recommend_quarter(
        QuarterRecommendationRequest(
            club_id=source.club_id,
            year=start.year,
            quarter=(start.month - 1) // 3 + 1,
            template_id=None
            if template_ids
            else body.template_id or getattr(source, "source_template_id", None),
            template_ids=template_ids,
            pricing_settings=body.pricing_settings,
            excluded_dates=body.excluded_dates,
            capacity=source.capacity or 20,
            minimum_entry_sessions=source.minimum_entry_sessions,
            refreshments_included=source.refreshments_included,
        ),
        db,
        source=source,
    )
