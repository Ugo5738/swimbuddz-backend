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
    today = on_date or date.today()
    if today > plan.period_end:
        return 0
    if today <= plan.period_start:
        return plan.sessions_included
    weekday_value = getattr(club.default_session_day, "value", club.default_session_day)
    target_weekday = _WEEKDAY_NUMBER.get(str(weekday_value).lower(), 5)
    offset = (target_weekday - today.weekday()) % 7
    first_session = today.fromordinal(today.toordinal() + offset)
    if first_session > plan.period_end:
        return 0
    calendar_occurrences = ((plan.period_end - first_session).days // 7) + 1
    return min(plan.sessions_included, calendar_occurrences)


def plan_price(
    plan: ClubPlanVersion, club: Club, *, on_date: date | None = None
) -> tuple[int, int, bool, str | None]:
    today = on_date or date.today()
    remaining = remaining_plan_sessions(plan, club, on_date=today)
    if today < plan.period_start:
        return plan.club_fee_kobo, remaining, True, None
    if remaining < plan.minimum_entry_sessions:
        return (
            0,
            remaining,
            False,
            (
                f"Club entry closes below {plan.minimum_entry_sessions} remaining "
                "sessions; use Community drop-ins until the next quarter"
            ),
        )
    amount = (
        plan.club_fee_kobo * remaining + plan.sessions_included - 1
    ) // plan.sessions_included
    return amount, remaining, True, None


def plan_response(plan: ClubPlanVersion, club: Club) -> ClubPlanResponse:
    amount, remaining, available, reason = plan_price(plan, club)
    values = {
        column.name: getattr(plan, column.name)
        for column in ClubPlanVersion.__table__.columns
    }
    pool_id = plan.pool_id or club.default_pool_id
    values["pool_id"] = pool_id
    values["operating_area_id"] = plan.operating_area_id or club.operating_area_id
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
