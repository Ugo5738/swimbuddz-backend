"""Club CRUD router.

  * GET /clubs            — public list (no auth) for picker autocomplete
  * GET /clubs/{id}       — public single record
  * POST /clubs           — admin only
  * PATCH /clubs/{id}     — admin only
  * DELETE /clubs/{id}    — admin only

Soft-FK relationships: club_id is referenced from club_challenges (and
potentially other tables in the future) without a hard FK, so deletion
just removes the row — challenges that pointed to it keep their stale
club_id but the picker filters them out by virtue of the club no longer
existing.
"""

import uuid
from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubApplicationPlan,
    ClubEnrollment,
    ClubPlanVersion,
    ClubReadinessAssessment,
    CommunityExperienceOffering,
    CommunityExperiencePurchase,
    Member,
    MemberMembership,
    Pod,
)
from services.members_service.schemas import (
    ActivateClubApplicationRequest,
    ClubApplicationCreate,
    ClubApplicationResponse,
    ClubCreate,
    ClubObservedAssessmentUpdate,
    ClubPaymentContext,
    ClubPlanCreate,
    ClubPlanResponse,
    ClubPreAssessmentUpsert,
    ClubResponse,
    ClubUpdate,
)
from services.members_service.routers._club_pricing import (
    application_response as _application_out,
    plan_price as _plan_price,
    plan_response as _plan_out,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/clubs", tags=["clubs"])
settings = get_settings()


async def _member_for_user(current_user: AuthUser, db: AsyncSession) -> Member:
    member = (
        await db.execute(select(Member).where(Member.auth_id == current_user.user_id))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=403, detail="Member profile not found")
    return member


@router.get("/", response_model=List[ClubResponse])
async def list_clubs(
    active_only: bool = Query(True, description="Hide inactive clubs (default true)."),
    db: AsyncSession = Depends(get_async_db),
):
    """List clubs. Public — used by the challenges admin form picker and
    any future club-scoped landing pages."""
    query = select(Club)
    if active_only:
        query = query.where(Club.is_active.is_(True))
    query = query.order_by(Club.name.asc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/plans", response_model=List[ClubPlanResponse])
async def list_club_plans(
    operating_area_id: Optional[uuid.UUID] = None,
    club_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """Return the currently purchasable, location-specific Club packages."""
    today = date.today()
    query = (
        select(ClubPlanVersion, Club)
        .join(Club, Club.id == ClubPlanVersion.club_id)
        .where(
            Club.is_active.is_(True),
            ClubPlanVersion.is_active.is_(True),
            ClubPlanVersion.effective_from <= today,
            (ClubPlanVersion.effective_to.is_(None))
            | (ClubPlanVersion.effective_to >= today),
            ClubPlanVersion.period_end >= today,
        )
    )
    if operating_area_id:
        query = query.where(Club.operating_area_id == operating_area_id)
    if club_id:
        query = query.where(Club.id == club_id)
    rows = (
        await db.execute(query.order_by(Club.name, ClubPlanVersion.period_start))
    ).all()
    return [_plan_out(plan, club) for plan, club in rows]


@router.get("/admin/plans", response_model=List[ClubPlanResponse])
async def list_all_club_plans(
    club_id: Optional[uuid.UUID] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(ClubPlanVersion, Club).join(Club, Club.id == ClubPlanVersion.club_id)
    if club_id:
        query = query.where(Club.id == club_id)
    rows = (
        await db.execute(query.order_by(Club.name, ClubPlanVersion.period_start.desc()))
    ).all()
    return [_plan_out(plan, club) for plan, club in rows]


@router.post(
    "/{club_id}/plans",
    response_model=ClubPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_club_plan(
    club_id: uuid.UUID,
    body: ClubPlanCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    club = await db.get(Club, club_id)
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")
    values = body.model_dump()
    if body.community_experience_offering_id:
        offering = await db.get(
            CommunityExperienceOffering, body.community_experience_offering_id
        )
        if offering is None or not offering.is_active:
            raise HTTPException(
                status_code=400, detail="Community Experience offering is unavailable"
            )
        if (
            offering.currency != body.currency
            or offering.period_start != body.period_start
            or offering.period_end != body.period_end
        ):
            raise HTTPException(
                status_code=400,
                detail="Community Experience and Club plan must use the same quarter and currency",
            )
        values["community_experience_fee_kobo"] = offering.club_bundle_fee_kobo
    plan = ClubPlanVersion(club_id=club_id, **values)
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return _plan_out(plan, club)


@router.post(
    "/applications",
    response_model=ClubApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_club_application(
    body: ClubApplicationCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _member_for_user(current_user, db)
    selected_ids = list(dict.fromkeys([body.plan_version_id, *body.plan_version_ids]))
    plans = list(
        (
            await db.execute(
                select(ClubPlanVersion).where(ClubPlanVersion.id.in_(selected_ids))
            )
        ).scalars()
    )
    plans_by_id = {plan.id: plan for plan in plans}
    plan = plans_by_id.get(body.plan_version_id)
    today = date.today()
    if (
        plan is None
        or not plan.is_active
        or plan.effective_from > today
        or (plan.effective_to and plan.effective_to < today)
    ):
        raise HTTPException(status_code=400, detail="This Club plan is not available")
    if len(plans) != len(selected_ids):
        raise HTTPException(
            status_code=400, detail="One or more Club plans are unavailable"
        )
    ordered_plans = [plans_by_id[plan_id] for plan_id in selected_ids]
    if any(selected.club_id != plan.club_id for selected in ordered_plans):
        raise HTTPException(
            status_code=400,
            detail="All prepaid quarters must belong to the same Club location",
        )
    if any(
        not selected.is_active
        or selected.effective_from > today
        or (selected.effective_to and selected.effective_to < today)
        for selected in ordered_plans
    ):
        raise HTTPException(
            status_code=400, detail="One or more Club plans are unavailable"
        )
    club = await db.get(Club, plan.club_id)
    if club is None:
        raise HTTPException(status_code=404, detail="Club location not found")
    current_plan = (
        (
            await db.execute(
                select(ClubPlanVersion)
                .where(
                    ClubPlanVersion.club_id == plan.club_id,
                    ClubPlanVersion.is_active.is_(True),
                    ClubPlanVersion.period_start <= today,
                    ClubPlanVersion.period_end >= today,
                    ClubPlanVersion.effective_from <= today,
                    (ClubPlanVersion.effective_to.is_(None))
                    | (ClubPlanVersion.effective_to >= today),
                )
                .order_by(ClubPlanVersion.period_start)
            )
        )
        .scalars()
        .first()
    )
    if (
        current_plan
        and _plan_price(current_plan, club)[2]
        and current_plan.id not in selected_ids
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The current Club quarter is still open and must be included. "
                "Future quarters remain optional."
            ),
        )
    if any(not _plan_price(selected, club)[2] for selected in ordered_plans):
        raise HTTPException(
            status_code=409,
            detail=(
                "This quarter is too close to closing for a new Club enrollment. "
                "Use Community drop-ins and select the next quarter instead."
            ),
        )
    chronologically = sorted(ordered_plans, key=lambda item: item.period_start)
    if any(
        current.period_start <= previous.period_end
        for previous, current in zip(
            chronologically,
            chronologically[1:],
        )
    ):
        raise HTTPException(status_code=400, detail="Selected Club quarters overlap")
    if body.preferred_pod_id:
        pod = await db.get(Pod, body.preferred_pod_id)
        if pod is None or pod.club_id != plan.club_id:
            raise HTTPException(
                status_code=400,
                detail="The selected pod does not belong to this Club location",
            )
    application = ClubApplication(
        member_id=member.id,
        club_id=plan.club_id,
        **body.model_dump(exclude={"plan_version_ids"}),
    )
    db.add(application)
    await db.flush()
    for index, selected in enumerate(chronologically):
        db.add(
            ClubApplicationPlan(
                application_id=application.id,
                plan_version_id=selected.id,
                sort_order=index,
            )
        )
    await db.commit()
    await db.refresh(application)
    return await _application_out(application, db)


@router.get("/applications/me", response_model=List[ClubApplicationResponse])
async def list_my_club_applications(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _member_for_user(current_user, db)
    applications = list(
        (
            await db.execute(
                select(ClubApplication)
                .where(ClubApplication.member_id == member.id)
                .order_by(ClubApplication.created_at.desc())
            )
        ).scalars()
    )
    return [await _application_out(application, db) for application in applications]


@router.put(
    "/applications/{application_id}/pre-assessment",
    response_model=ClubApplicationResponse,
)
async def submit_club_pre_assessment(
    application_id: uuid.UUID,
    body: ClubPreAssessmentUpsert,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _member_for_user(current_user, db)
    application = await db.get(ClubApplication, application_id)
    if application is None or application.member_id != member.id:
        raise HTTPException(status_code=404, detail="Club application not found")
    assessment = (
        await db.execute(
            select(ClubReadinessAssessment).where(
                ClubReadinessAssessment.application_id == application.id
            )
        )
    ).scalar_one_or_none()
    if assessment is None:
        assessment = ClubReadinessAssessment(application_id=application.id)
        db.add(assessment)
    assessment.self_report = body.model_dump(mode="json")
    application.status = "assessment_pending"
    await db.commit()
    await db.refresh(application)
    return await _application_out(application, db)


@router.get("/admin/applications", response_model=List[ClubApplicationResponse])
async def list_club_applications_for_review(
    application_status: Optional[str] = Query(default=None, alias="status"),
    club_id: Optional[uuid.UUID] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(ClubApplication)
    if application_status:
        query = query.where(ClubApplication.status == application_status)
    if club_id:
        query = query.where(ClubApplication.club_id == club_id)
    applications = list(
        (await db.execute(query.order_by(ClubApplication.created_at.desc()))).scalars()
    )
    return [await _application_out(application, db) for application in applications]


@router.put(
    "/admin/applications/{application_id}/assessment",
    response_model=ClubApplicationResponse,
)
async def complete_observed_club_assessment(
    application_id: uuid.UUID,
    body: ClubObservedAssessmentUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    assessor = await _member_for_user(current_user, db)
    application = await db.get(ClubApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Club application not found")
    assessment = (
        await db.execute(
            select(ClubReadinessAssessment).where(
                ClubReadinessAssessment.application_id == application.id
            )
        )
    ).scalar_one_or_none()
    if assessment is None:
        assessment = ClubReadinessAssessment(application_id=application.id)
        db.add(assessment)
    for field, value in body.model_dump(exclude={"send_result_email"}).items():
        setattr(assessment, field, value)
    assessment.assessor_member_id = assessor.id
    assessment.completed_at = utc_now()
    application.status = (
        "approved"
        if body.outcome in {"club_ready", "club_ready_modified"}
        else "academy_recommended"
    )
    await db.commit()

    if body.send_result_email:
        member = await db.get(Member, application.member_id)
        club = await db.get(Club, application.club_id)
        outcome_copy = {
            "club_ready": "You are Club-ready.",
            "club_ready_modified": "You are Club-ready with modified participation while we rebuild your fundamentals.",
            "academy_first": "We recommend the Academy first so you can build the safety and technique base needed for Club practice.",
        }[body.outcome]
        lines = [
            f"Hi {member.first_name},",
            "",
            outcome_copy,
            f"Location: {club.name}",
        ]
        if body.primary_technique_focus:
            lines.append(f"Primary technique focus: {body.primary_technique_focus}")
        if body.first_club_milestone:
            lines.append(f"First milestone: {body.first_club_milestone}")
        if application.status == "approved":
            lines.extend(
                [
                    "",
                    "Sign in to review your server-calculated Club quote. It will show "
                    "each selected quarter, any mid-quarter adjustment, annual "
                    "SwimBuddz Membership due, and the optional Community Experience.",
                ]
            )
        sent = await get_email_client().send(
            to_email=member.email,
            subject="Your SwimBuddz Club readiness result",
            body="\n".join(lines),
        )
        if sent:
            assessment.result_email_sent_at = utc_now()
            await db.commit()
    await db.refresh(application)
    return await _application_out(application, db)


@router.get(
    "/internal/applications/{application_id}/payment-context",
    response_model=ClubPaymentContext,
)
async def get_club_application_payment_context(
    application_id: uuid.UUID,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    application = await db.get(ClubApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Club application not found")
    if application.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Club assessment approval is required before payment",
        )
    member = await db.get(Member, application.member_id)
    club = await db.get(Club, application.club_id)
    plan = await db.get(ClubPlanVersion, application.plan_version_id)
    membership = (
        await db.execute(
            select(MemberMembership).where(MemberMembership.member_id == member.id)
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
    selected_plans = [selected_plan for _row, selected_plan in selections]
    if not selected_plans:
        selected_plans = [plan]
    club_items: list[dict] = []
    for selected_plan in selected_plans:
        amount, remaining, available, reason = _plan_price(selected_plan, club)
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        club_items.append(
            {
                "plan_version_id": str(selected_plan.id),
                "name": selected_plan.name,
                "period_start": selected_plan.period_start.isoformat(),
                "period_end": selected_plan.period_end.isoformat(),
                "sessions_included": selected_plan.sessions_included,
                "remaining_sessions": remaining,
                "full_quarter_fee_kobo": selected_plan.club_fee_kobo,
                "amount_kobo": amount,
            }
        )
    club_fee = sum(item["amount_kobo"] for item in club_items)
    last_period_end = max(selected.period_end for selected in selected_plans)
    membership_covers_selection = bool(
        membership
        and membership.community_paid_until
        and membership.community_paid_until.date() >= last_period_end
    )
    annual_membership_months = 0 if membership_covers_selection else 12
    annual_membership_fee = (
        0
        if membership_covers_selection
        else int(getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20_000) * 100)
    )
    experience_fee = 0
    experience_selected = application.community_experience_selected
    primary_offering = (
        await db.get(CommunityExperienceOffering, plan.community_experience_offering_id)
        if plan.community_experience_offering_id
        else None
    )
    if experience_selected and primary_offering:
        now = utc_now()
        purchasable = (
            primary_offering.is_active
            and (
                primary_offering.purchase_opens_at is None
                or primary_offering.purchase_opens_at <= now
            )
            and (
                primary_offering.purchase_closes_at is None
                or primary_offering.purchase_closes_at >= now
            )
        )
        if not purchasable:
            experience_selected = False
        else:
            experience_fee = primary_offering.club_bundle_fee_kobo
    elif experience_selected:
        # Backwards compatibility for plans created before offerings existed.
        experience_fee = plan.community_experience_fee_kobo
    if experience_selected and primary_offering:
        existing_experience = (
            await db.execute(
                select(CommunityExperiencePurchase.id).where(
                    CommunityExperiencePurchase.member_id == member.id,
                    CommunityExperiencePurchase.offering_id == primary_offering.id,
                )
            )
        ).first()
        if existing_experience:
            experience_selected = False
            experience_fee = 0
    subtotal = club_fee + annual_membership_fee + experience_fee
    if subtotal <= 0:
        raise HTTPException(
            status_code=409,
            detail="Nothing remains payable for this Club application",
        )
    return ClubPaymentContext(
        application_id=application.id,
        member_auth_id=member.auth_id,
        club_id=club.id,
        club_name=club.name,
        plan_version_id=plan.id,
        plan_version_ids=[selected.id for selected in selected_plans],
        billing_cycle=plan.billing_cycle,
        currency=plan.currency,
        club_fee_kobo=club_fee,
        club_items=club_items,
        annual_membership_fee_kobo=annual_membership_fee,
        annual_membership_months=annual_membership_months,
        community_experience_selected=experience_selected,
        community_experience_fee_kobo=experience_fee,
        subtotal_kobo=subtotal,
        months=3 * len(selected_plans),
    )


@router.post(
    "/internal/applications/{application_id}/activate",
    response_model=ClubApplicationResponse,
)
async def activate_club_application(
    application_id: uuid.UUID,
    body: ActivateClubApplicationRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    application = await db.get(ClubApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Club application not found")
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
    selected_plans = [selected_plan for _row, selected_plan in selections]
    if not selected_plans:
        selected_plans = [await db.get(ClubPlanVersion, application.plan_version_id)]
    created_enrollments: list[ClubEnrollment] = []
    for selected_plan in selected_plans:
        existing = (
            await db.execute(
                select(ClubEnrollment).where(
                    ClubEnrollment.application_id == application.id,
                    ClubEnrollment.plan_version_id == selected_plan.id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            created_enrollments.append(existing)
            continue
        start_date = max(date.today(), selected_plan.period_start)
        enrollment = ClubEnrollment(
            member_id=application.member_id,
            club_id=application.club_id,
            plan_version_id=selected_plan.id,
            application_id=application.id,
            payment_reference=body.payment_reference,
            starts_at=datetime.combine(start_date, time.min, tzinfo=timezone.utc),
            ends_at=datetime.combine(
                selected_plan.period_end.fromordinal(
                    selected_plan.period_end.toordinal() + 1
                ),
                time.min,
                tzinfo=timezone.utc,
            ),
            assigned_pod_id=application.preferred_pod_id,
        )
        db.add(enrollment)
        await db.flush()
        created_enrollments.append(enrollment)
    primary_plan = selected_plans[0]
    if (
        body.community_experience_selected
        and primary_plan.community_experience_offering_id
    ):
        offering = await db.get(
            CommunityExperienceOffering,
            primary_plan.community_experience_offering_id,
        )
        existing_purchase = (
            await db.execute(
                select(CommunityExperiencePurchase).where(
                    CommunityExperiencePurchase.member_id == application.member_id,
                    CommunityExperiencePurchase.offering_id == offering.id,
                )
            )
        ).scalar_one_or_none()
        if existing_purchase is None:
            db.add(
                CommunityExperiencePurchase(
                    member_id=application.member_id,
                    offering_id=offering.id,
                    club_enrollment_id=(
                        created_enrollments[0].id if created_enrollments else None
                    ),
                    price_context="club_bundle",
                    amount_paid_kobo=body.community_experience_fee_kobo,
                    payment_reference=body.payment_reference,
                )
            )
    application.status = "enrolled"
    await db.commit()
    await db.refresh(application)
    return await _application_out(application, db)


@router.get("/{club_id}", response_model=ClubResponse)
async def get_club(
    club_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    return club


@router.post("/", response_model=ClubResponse, status_code=201)
async def create_club(
    body: ClubCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    club = Club(**body.model_dump())
    db.add(club)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Slug already taken — pick something unique.",
        ) from exc
    await db.refresh(club)
    return club


@router.patch("/{club_id}", response_model=ClubResponse)
async def update_club(
    club_id: uuid.UUID,
    body: ClubUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club: Optional[Club] = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(club, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Slug already taken — pick something unique.",
        ) from exc
    await db.refresh(club)
    return club


@router.delete("/{club_id}", status_code=204)
async def delete_club(
    club_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club: Optional[Club] = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    await db.delete(club)
    await db.commit()
    return None
