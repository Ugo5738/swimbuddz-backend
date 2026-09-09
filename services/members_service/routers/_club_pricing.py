"""Shared Club quarter pricing and response helpers."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    Club,
    ClubApplication,
    ClubApplicationPlan,
    ClubPlanVersion,
    ClubReadinessAssessment,
    Member,
)
from services.members_service.schemas import (
    ClubApplicationResponse,
    ClubAssessmentResponse,
    ClubPlanResponse,
)
from services.members_service.services.club_plan_schedule import (
    actual_plan_price,
    hydrate_schedules,
    remaining_links,
)


_WEEKDAY_NUMBER = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def remaining_plan_sessions(
    plan: ClubPlanVersion, club: Club, *, on_date: date | None = None
) -> int:
    return len(remaining_links(plan, on_date=on_date))


def plan_price(
    plan: ClubPlanVersion, club: Club, *, on_date: date | None = None
) -> tuple[int, int, bool, str | None]:
    return actual_plan_price(plan, on_date=on_date)


def plan_response(plan: ClubPlanVersion, club: Club) -> ClubPlanResponse:
    amount, remaining, available, reason = plan_price(plan, club)
    values = {
        column.name: getattr(plan, column.name)
        for column in ClubPlanVersion.__table__.columns
    }
    pool_id = plan.pool_id or club.default_pool_id
    values["pool_id"] = pool_id
    values["operating_area_id"] = plan.operating_area_id or club.operating_area_id
    values["session_ids"] = [
        link.session_id for link in getattr(plan, "session_links", [])
    ]
    return ClubPlanResponse(
        **values,
        club_name=club.name,
        club_slug=club.slug,
        location=club.location,
        default_pool_id=pool_id,
        remaining_sessions=remaining,
        entry_available=available,
        entry_reason=reason,
        current_price_kobo=amount,
    )


async def application_response(
    application: ClubApplication, db: AsyncSession
) -> ClubApplicationResponse:
    plan = await db.get(ClubPlanVersion, application.plan_version_id)
    club = await db.get(Club, application.club_id)
    member = await db.get(Member, application.member_id)
    assessment = (
        await db.execute(
            select(ClubReadinessAssessment).where(
                ClubReadinessAssessment.application_id == application.id
            )
        )
    ).scalar_one_or_none()
    selections = list(
        (
            await db.execute(
                select(ClubApplicationPlan, ClubPlanVersion)
                .join(
                    ClubPlanVersion,
                    ClubPlanVersion.id == ClubApplicationPlan.plan_version_id,
                )
                .where(ClubApplicationPlan.application_id == application.id)
                .order_by(ClubApplicationPlan.sort_order)
            )
        ).all()
    )
    await hydrate_schedules([plan] + [selected for _, selected in selections])
    selected_plans = [
        plan_response(selected_plan, club)
        for _selection, selected_plan in selections
        if club is not None
    ]
    if not selected_plans and plan and club:
        selected_plans = [plan_response(plan, club)]
    return ClubApplicationResponse(
        **{
            column.name: getattr(application, column.name)
            for column in ClubApplication.__table__.columns
        },
        plan=plan_response(plan, club) if plan and club else None,
        selected_plans=selected_plans,
        member_name=(f"{member.first_name} {member.last_name}" if member else None),
        member_email=(member.email if member else None),
        assessment=(
            ClubAssessmentResponse.model_validate(assessment) if assessment else None
        ),
    )
