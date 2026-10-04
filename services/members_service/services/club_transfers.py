"""Domain service for permanent Club home-location transfers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubApplicationPlan,
    ClubEnrollment,
    ClubEnrollmentTransfer,
    ClubPlanVersion,
    ClubReadinessAssessment,
    Member,
    Pod,
    PodAssignment,
    PodAssignmentSource,
    PodStatus,
)
from services.members_service.schemas.club import (
    AdminClubEnrollmentSummary,
    ClubLocationTransferPreview,
    ClubLocationTransferRequest,
    ClubLocationTransferResponse,
)
from services.members_service.services.chat_sync import reconcile_pod_membership
from services.members_service.services.club_plan_schedule import (
    hydrate_schedules,
    remaining_links,
)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(422, "effective_at must include a timezone")
    return value.astimezone(timezone.utc)


def _effective_at(body: ClubLocationTransferRequest) -> datetime:
    return _as_aware(body.effective_at) if body.effective_at else utc_now()


def _remaining_value(plan: ClubPlanVersion, *, on_date) -> tuple[int, int]:
    """Return remaining included swims and their proportional quarter value."""
    remaining = remaining_links(plan, on_date=on_date)
    links = list(getattr(plan, "session_links", []) or [])
    if not links:
        return 0, 0
    total_weight = sum(int(link.fee_kobo or 0) for link in links)
    remaining_weight = sum(int(link.fee_kobo or 0) for link in remaining)
    if total_weight == 0:
        total_weight = len(links)
        remaining_weight = len(remaining)
    value = (
        int(plan.club_fee_kobo) * remaining_weight + total_weight - 1
    ) // total_weight
    return len(remaining), value


async def _load_context(
    db: AsyncSession,
    source_enrollment_id: uuid.UUID,
    body: ClubLocationTransferRequest,
    *,
    lock: bool,
) -> dict:
    source_query = select(ClubEnrollment).where(
        ClubEnrollment.id == source_enrollment_id
    )
    if lock:
        source_query = source_query.with_for_update()
    source = (await db.execute(source_query)).scalar_one_or_none()
    if source is None:
        raise HTTPException(404, "Club enrollment not found")

    effective = _effective_at(body)
    now = utc_now()
    if source.status != "active" or not (
        source.starts_at <= effective < source.ends_at
    ):
        raise HTTPException(
            409, "The source Club enrollment must be active on the transfer date"
        )
    if effective > now + timedelta(minutes=5):
        raise HTTPException(
            422,
            "Location transfers are immediate. Use cross-location visits until the intended move date.",
        )
    if effective <= source.starts_at:
        raise HTTPException(
            409, "The transfer date must be after the source enrollment started"
        )

    target_query = select(ClubPlanVersion).where(
        ClubPlanVersion.id == body.target_plan_version_id
    )
    if lock:
        target_query = target_query.with_for_update()
    target_plan = (await db.execute(target_query)).scalar_one_or_none()
    if target_plan is None:
        raise HTTPException(404, "Target Club plan not found")

    target_club = await db.get(Club, target_plan.club_id)
    source_club = await db.get(Club, source.club_id)
    source_plan = await db.get(ClubPlanVersion, source.plan_version_id)
    member = await db.get(Member, source.member_id)
    if not all([target_club, source_club, source_plan, member]):
        raise HTTPException(409, "Club transfer records are incomplete")
    if target_club.id == source.club_id:
        raise HTTPException(
            409,
            "Choose another Club location. Pod changes inside one Club use Pod Transfer.",
        )

    effective_date = effective.date()
    entitlement_end_date = (source.ends_at - timedelta(seconds=1)).date()
    if (
        not target_club.is_active
        or not target_plan.is_active
        or target_plan.published_at is None
        or target_plan.period_start > effective_date
        or target_plan.period_end < entitlement_end_date
        or target_plan.effective_from > effective_date
        or (
            target_plan.effective_to is not None
            and target_plan.effective_to < entitlement_end_date
        )
    ):
        raise HTTPException(
            409,
            "Choose a published target Club plan that covers the member's full remaining access period",
        )

    target_pod = None
    if body.target_pod_id is not None:
        pod_query = select(Pod).where(Pod.id == body.target_pod_id)
        if lock:
            pod_query = pod_query.with_for_update()
        target_pod = (await db.execute(pod_query)).scalar_one_or_none()
        if (
            target_pod is None
            or target_pod.club_id != target_club.id
            or target_pod.status != PodStatus.ACTIVE
        ):
            raise HTTPException(
                409, "Choose an active pod that belongs to the target Club location"
            )
        active_count = int(
            (
                await db.execute(
                    select(func.count(PodAssignment.id)).where(
                        PodAssignment.pod_id == target_pod.id,
                        PodAssignment.left_at.is_(None),
                    )
                )
            ).scalar_one()
            or 0
        )
        if active_count >= target_pod.max_size:
            raise HTTPException(409, "The target pod is full")

    return {
        "source": source,
        "source_club": source_club,
        "source_plan": source_plan,
        "target_club": target_club,
        "target_plan": target_plan,
        "target_pod": target_pod,
        "member": member,
        "effective": effective,
    }


async def list_member_enrollments(
    db: AsyncSession, member_id: uuid.UUID
) -> list[AdminClubEnrollmentSummary]:
    rows = (
        await db.execute(
            select(ClubEnrollment, Club, ClubPlanVersion)
            .join(Club, Club.id == ClubEnrollment.club_id)
            .join(
                ClubPlanVersion,
                ClubPlanVersion.id == ClubEnrollment.plan_version_id,
            )
            .where(ClubEnrollment.member_id == member_id)
            .order_by(ClubEnrollment.starts_at.desc())
        )
    ).all()
    return [
        AdminClubEnrollmentSummary(
            id=enrollment.id,
            member_id=enrollment.member_id,
            club_id=enrollment.club_id,
            club_name=club.name,
            plan_version_id=enrollment.plan_version_id,
            plan_name=plan.name,
            payment_mode=enrollment.payment_mode,
            starts_at=enrollment.starts_at,
            ends_at=enrollment.ends_at,
            status=enrollment.status,
            assigned_pod_id=enrollment.assigned_pod_id,
        )
        for enrollment, club, plan in rows
    ]


async def preview_location_transfer(
    db: AsyncSession,
    source_enrollment_id: uuid.UUID,
    body: ClubLocationTransferRequest,
) -> ClubLocationTransferPreview:
    ctx = await _load_context(db, source_enrollment_id, body, lock=False)
    source = ctx["source"]
    source_plan = ctx["source_plan"]
    target_plan = ctx["target_plan"]
    effective = ctx["effective"]

    # Network hydration is preview-only and deliberately happens without DB row
    # locks. Execution re-locks and revalidates the authoritative rows later.
    await hydrate_schedules([source_plan, target_plan])
    source_count, source_value = _remaining_value(
        source_plan, on_date=effective.date()
    )
    target_count, target_value = _remaining_value(
        target_plan, on_date=effective.date()
    )
    quarterly = source.payment_mode == "quarterly_prepaid"
    return ClubLocationTransferPreview(
        source_enrollment_id=source.id,
        member_id=source.member_id,
        source_club_id=ctx["source_club"].id,
        source_club_name=ctx["source_club"].name,
        target_club_id=ctx["target_club"].id,
        target_club_name=ctx["target_club"].name,
        target_plan_version_id=target_plan.id,
        target_plan_name=target_plan.name,
        target_pod_id=ctx["target_pod"].id if ctx["target_pod"] else None,
        payment_mode=source.payment_mode,
        effective_at=effective,
        source_remaining_sessions=source_count,
        source_remaining_value_kobo=source_value,
        target_remaining_sessions=target_count,
        target_remaining_value_kobo=target_value,
        estimated_difference_kobo=target_value - source_value,
        can_execute_now=not quarterly,
        requires_financial_reconciliation=quarterly,
        guidance=(
            "This transition-per-session membership can move now. No second "
            "quarter fee is collected; future sessions use the target Club's "
            "per-session prices."
            if not quarterly
            else "This member prepaid a quarter. Use cross-location visits for "
            "temporary attendance or reconcile the remaining-value difference "
            "before granting a replacement prepaid quarter."
        ),
    )


async def execute_location_transfer(
    db: AsyncSession,
    source_enrollment_id: uuid.UUID,
    body: ClubLocationTransferRequest,
    *,
    requested_by_auth_id: str,
) -> ClubLocationTransferResponse:
    preview = await preview_location_transfer(db, source_enrollment_id, body)
    if preview.requires_financial_reconciliation:
        raise HTTPException(
            409,
            {
                "message": (
                    "Prepaid-quarter location transfers require financial "
                    "reconciliation before the home Club can change."
                ),
                "preview": preview.model_dump(mode="json"),
            },
        )

    ctx = await _load_context(db, source_enrollment_id, body, lock=True)
    source: ClubEnrollment = ctx["source"]
    target_plan: ClubPlanVersion = ctx["target_plan"]
    target_club: Club = ctx["target_club"]
    target_pod: Pod | None = ctx["target_pod"]
    effective: datetime = ctx["effective"]

    open_application = (
        (
            await db.execute(
                select(ClubApplication).where(
                    ClubApplication.member_id == source.member_id,
                    ClubApplication.status.in_(
                        {"assessment_required", "assessment_pending", "approved"}
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    if open_application is not None:
        raise HTTPException(
            409,
            "Resolve the member's existing open Club application before changing location",
        )

    source_application = await db.get(ClubApplication, source.application_id)
    readiness = (
        await db.execute(
            select(ClubReadinessAssessment).where(
                ClubReadinessAssessment.application_id == source.application_id
            )
        )
    ).scalar_one_or_none()
    if (
        source_application is None
        or readiness is None
        or readiness.outcome not in {"club_ready", "club_ready_modified"}
    ):
        raise HTTPException(
            409, "The current enrollment has no reusable Club readiness decision"
        )

    transfer_id = uuid.uuid4()
    original_end = source.ends_at
    target_application = ClubApplication(
        member_id=source.member_id,
        club_id=target_club.id,
        plan_version_id=target_plan.id,
        status="enrolled",
        community_experience_selected=False,
        preferred_pod_id=target_pod.id if target_pod else None,
        notes=(
            f"Home Club transfer from {ctx['source_club'].name}. "
            f"{(body.note or '').strip()}"
        ).strip(),
        approved_payment_modes=["transition_per_session"],
        transition_expires_at=(original_end - timedelta(seconds=1)).date(),
        selected_payment_mode="transition_per_session",
    )
    db.add(target_application)
    await db.flush()
    db.add(
        ClubApplicationPlan(
            application_id=target_application.id,
            plan_version_id=target_plan.id,
            sort_order=0,
        )
    )
    db.add(
        ClubReadinessAssessment(
            application_id=target_application.id,
            self_report={
                **dict(readiness.self_report or {}),
                "readiness_reused_from_application_id": str(source.application_id),
                "location_transfer_id": str(transfer_id),
            },
            observed_checks=dict(readiness.observed_checks or {}),
            assessor_member_id=readiness.assessor_member_id,
            outcome=readiness.outcome,
            nonstop_distance_m=readiness.nonstop_distance_m,
            deep_water_comfort=readiness.deep_water_comfort,
            primary_technique_focus=readiness.primary_technique_focus,
            first_club_milestone=readiness.first_club_milestone,
            assessor_notes=(
                "Readiness reused for a permanent Club location transfer. "
                f"Source application: {source.application_id}."
            ),
            completed_at=readiness.completed_at or utc_now(),
        )
    )

    from services.members_service.routers.clubs import (
        _assert_plan_capacity,
        _assert_pod_capacity,
    )

    await _assert_plan_capacity(
        db, application=target_application, plans=[target_plan], at=effective
    )
    await _assert_pod_capacity(
        db, application=target_application, at=effective, lock=True
    )

    target_enrollment = ClubEnrollment(
        member_id=source.member_id,
        club_id=target_club.id,
        plan_version_id=target_plan.id,
        pool_id=target_plan.pool_id or target_club.default_pool_id,
        operating_area_id=(
            target_plan.operating_area_id or target_club.operating_area_id
        ),
        application_id=target_application.id,
        payment_reference=f"club-transfer:{transfer_id}",
        starts_at=effective,
        ends_at=original_end,
        payment_mode="transition_per_session",
        assigned_pod_id=target_pod.id if target_pod else None,
        status="active",
    )
    db.add(target_enrollment)
    await db.flush()

    old_assignment_row = (
        await db.execute(
            select(PodAssignment, Pod)
            .join(Pod, Pod.id == PodAssignment.pod_id)
            .where(
                PodAssignment.member_id == source.member_id,
                PodAssignment.left_at.is_(None),
            )
            .with_for_update()
        )
    ).one_or_none()
    old_assignment = old_assignment_row[0] if old_assignment_row else None
    if old_assignment is not None:
        old_assignment.left_at = effective

    new_assignment = None
    if target_pod is not None:
        new_assignment = PodAssignment(
            pod_id=target_pod.id,
            member_id=source.member_id,
            assigned_by=PodAssignmentSource.ADMIN,
            assigned_by_id=None,
            joined_at=effective,
        )
        db.add(new_assignment)

    source.ends_at = effective
    source.status = "transferred"
    transfer = ClubEnrollmentTransfer(
        id=transfer_id,
        member_id=source.member_id,
        source_enrollment_id=source.id,
        target_enrollment_id=target_enrollment.id,
        source_club_id=source.club_id,
        target_club_id=target_club.id,
        target_plan_version_id=target_plan.id,
        target_pod_id=target_pod.id if target_pod else None,
        effective_at=effective,
        source_payment_mode=source.payment_mode,
        status="completed",
        requested_by_auth_id=requested_by_auth_id,
        note=(body.note or "").strip() or None,
    )
    db.add(transfer)
    await db.commit()

    if old_assignment is not None:
        await reconcile_pod_membership(
            pod_id=old_assignment.pod_id,
            member_id=source.member_id,
            assignment_id=old_assignment.id,
            action="remove",
        )
    if new_assignment is not None:
        await db.refresh(new_assignment)
        await reconcile_pod_membership(
            pod_id=new_assignment.pod_id,
            member_id=source.member_id,
            assignment_id=new_assignment.id,
            action="add",
        )

    return ClubLocationTransferResponse(
        **preview.model_dump(),
        transfer_id=transfer.id,
        target_enrollment_id=target_enrollment.id,
    )
