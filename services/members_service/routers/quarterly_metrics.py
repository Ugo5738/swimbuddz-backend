"""Domain-owned Club, pod, plan and Experience reporting (service JWT only)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, reporting_window
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubEnrollment,
    ClubPlanVersion,
    Member,
    Pod,
    PodAssignment,
    CommunityExperienceOrder,
    CommunityExperienceParticipant,
    CommunityExperienceAttendance,
    CommunityExperiencePurchase,
)

router = APIRouter(prefix="/internal/members", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    member_auth_id: str | None = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    member_id = None
    if member_auth_id:
        member_id = (
            await db.execute(select(Member.id).where(Member.auth_id == member_auth_id))
        ).scalar_one_or_none()
        if member_id is None:
            return ReportingSection(
                available=False, notes=["Member profile is unavailable."]
            )

    clubs = (
        (await db.execute(select(Club).where(Club.created_at < end))).scalars().all()
    )
    pods = (
        (
            await db.execute(
                select(Pod).where(
                    Pod.created_at < end,
                    or_(Pod.dissolved_at.is_(None), Pod.dissolved_at >= end),
                )
            )
        )
        .scalars()
        .all()
    )
    assignment_query = select(PodAssignment).where(
        PodAssignment.joined_at < end,
        or_(PodAssignment.left_at.is_(None), PodAssignment.left_at > start),
    )
    enrollment_query = select(ClubEnrollment).where(
        ClubEnrollment.starts_at < end,
        ClubEnrollment.ends_at >= start,
        ClubEnrollment.status == "active",
    )
    if member_id:
        assignment_query = assignment_query.where(PodAssignment.member_id == member_id)
        enrollment_query = enrollment_query.where(ClubEnrollment.member_id == member_id)
    assignments = (await db.execute(assignment_query)).scalars().all()
    enrollments = (await db.execute(enrollment_query)).scalars().all()
    pod_ids_at_end = {p.id for p in pods}
    end_assignments = [
        a
        for a in assignments
        if (a.left_at is None or a.left_at >= end) and a.pod_id in pod_ids_at_end
    ]
    populated_end = {a.pod_id for a in end_assignments}
    club_rows = []
    for club in clubs:
        club_pods = [p for p in pods if p.club_id == club.id]
        club_enrollments = [e for e in enrollments if e.club_id == club.id]
        if (
            member_id
            and not club_enrollments
            and not any(a.pod_id in {p.id for p in club_pods} for a in assignments)
        ):
            continue
        club_rows.append(
            {
                "club_id": str(club.id),
                "name": club.name,
                "location": club.location,
                "pool_id": str(club.default_pool_id) if club.default_pool_id else None,
                "operating_area_id": str(club.operating_area_id)
                if club.operating_area_id
                else None,
                "currently_active": club.is_active,
                "pods_configured": len(club_pods),
                "pods_populated_at_end": sum(p.id in populated_end for p in club_pods),
                "enrolled_members": len({e.member_id for e in club_enrollments}),
                "prepaid_enrollments": sum(
                    e.payment_mode == "quarterly_prepaid" for e in club_enrollments
                ),
                "transition_enrollments": sum(
                    e.payment_mode == "transition_per_session" for e in club_enrollments
                ),
            }
        )
    plans = (
        (
            await db.execute(
                select(ClubPlanVersion).where(
                    ClubPlanVersion.published_at < end,
                    ClubPlanVersion.period_start <= date_to.date(),
                    ClubPlanVersion.period_end >= date_from.date(),
                )
            )
        )
        .scalars()
        .all()
        if not member_id
        else []
    )
    orders_query = select(CommunityExperienceOrder).where(
        CommunityExperienceOrder.created_at >= start,
        CommunityExperienceOrder.created_at < end,
        CommunityExperienceOrder.status == "confirmed",
    )
    purchases_query = select(CommunityExperiencePurchase).where(
        CommunityExperiencePurchase.created_at >= start,
        CommunityExperiencePurchase.created_at < end,
        CommunityExperiencePurchase.status == "active",
    )
    checkins_query = (
        select(CommunityExperienceAttendance)
        .join(
            CommunityExperienceParticipant,
            CommunityExperienceParticipant.id
            == CommunityExperienceAttendance.participant_id,
        )
        .where(
            CommunityExperienceAttendance.checked_in_at >= start,
            CommunityExperienceAttendance.checked_in_at < end,
        )
    )
    if member_id:
        orders_query = orders_query.where(
            CommunityExperienceOrder.member_id == member_id
        )
        purchases_query = purchases_query.where(
            CommunityExperiencePurchase.member_id == member_id
        )
        checkins_query = checkins_query.where(
            CommunityExperienceParticipant.member_id == member_id
        )
    orders = (await db.execute(orders_query)).scalars().all()
    purchases = (await db.execute(purchases_query)).scalars().all()
    checkins = (await db.execute(checkins_query)).scalars().all()
    # Count named tickets in confirmed orders, never pending checkout seats.
    tickets = (
        (
            await db.execute(
                select(CommunityExperienceParticipant.id).where(
                    CommunityExperienceParticipant.order_id.in_([o.id for o in orders]),
                )
            )
        )
        .scalars()
        .all()
        if orders
        else []
    )
    metrics = {
        "clubs_configured": len(club_rows),
        "clubs_with_enrollments": len({e.club_id for e in enrollments}),
        "pods_configured": len(pods)
        if not member_id
        else len({a.pod_id for a in assignments}),
        "pods_with_assignments": len({a.pod_id for a in assignments}),
        "pods_populated_at_end": len(populated_end),
        "pod_members_in_period": len({a.member_id for a in assignments}),
        "pod_members_at_end": len({a.member_id for a in end_assignments}),
        "club_enrolled_members": len({e.member_id for e in enrollments}),
        "club_prepaid_enrollments": sum(
            e.payment_mode == "quarterly_prepaid" for e in enrollments
        ),
        "club_transition_enrollments": sum(
            e.payment_mode == "transition_per_session" for e in enrollments
        ),
        "experience_confirmed_orders": len(orders),
        "experience_named_tickets": len(tickets),
        "experience_package_purchases": len(purchases),
        "experience_checkins": len(checkins),
        "experience_unique_attendees": len({a.participant_id for a in checkins}),
    }
    if not member_id:
        metrics.update(
            {
                "new_clubs": sum(c.created_at >= start for c in clubs),
                "new_pods": sum(p.created_at >= start for p in pods),
                "club_published_plans": len(plans),
                "club_plans_with_pool": sum(p.pool_id is not None for p in plans),
                "club_plans_with_area": sum(
                    p.operating_area_id is not None for p in plans
                ),
            }
        )
    return ReportingSection(
        metrics=metrics,
        details={
            "clubs": club_rows,
            "pods": [
                {
                    "pod_id": str(p.id),
                    "name": p.name,
                    "club_id": str(p.club_id),
                    "members_at_end": len(
                        {a.member_id for a in end_assignments if a.pod_id == p.id}
                    ),
                }
                for p in pods
                if not member_id or p.id in {a.pod_id for a in assignments}
            ],
            "plans": [
                {
                    "plan_id": str(p.id),
                    "club_id": str(p.club_id),
                    "name": p.name,
                    "pool_id": str(p.pool_id) if p.pool_id else None,
                    "operating_area_id": str(p.operating_area_id)
                    if p.operating_area_id
                    else None,
                    "period_start": p.period_start.isoformat(),
                    "period_end": p.period_end.isoformat(),
                }
                for p in plans
            ],
        },
        notes=[
            "Configured Clubs do not establish operating venues. Club active status and enrollment status are current state; historical status changes are not recorded.",
            "Pods are configured at quarter-end; assignment counts use joined/left dates. New pods exclude those dissolved before quarter-end.",
            "Experience orders use booking date and current confirmed status; package purchases are separate entitlements and must not be added to named tickets. Check-ins use event check-in time.",
        ],
    )
