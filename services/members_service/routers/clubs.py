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
from datetime import date
from typing import List, Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubApplication,
    ClubEnrollment,
    ClubPlanVersion,
    ClubReadinessAssessment,
    Member,
    MemberMembership,
    Pod,
)
from services.members_service.schemas import (
    ActivateClubApplicationRequest,
    ClubApplicationCreate,
    ClubApplicationResponse,
    ClubAssessmentResponse,
    ClubCreate,
    ClubObservedAssessmentUpdate,
    ClubPaymentContext,
    ClubPlanCreate,
    ClubPlanResponse,
    ClubPreAssessmentUpsert,
    ClubResponse,
    ClubUpdate,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/clubs", tags=["clubs"])


async def _member_for_user(current_user: AuthUser, db: AsyncSession) -> Member:
    member = (
        await db.execute(select(Member).where(Member.auth_id == current_user.user_id))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=403, detail="Member profile not found")
    return member


def _plan_out(plan: ClubPlanVersion, club: Club) -> ClubPlanResponse:
    return ClubPlanResponse(
        **{
            column.name: getattr(plan, column.name)
            for column in ClubPlanVersion.__table__.columns
        },
        club_name=club.name,
        club_slug=club.slug,
        location=club.location,
        operating_area_id=club.operating_area_id,
        default_pool_id=club.default_pool_id,
    )


async def _application_out(
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
    return ClubApplicationResponse(
        **{
            column.name: getattr(application, column.name)
            for column in ClubApplication.__table__.columns
        },
        plan=_plan_out(plan, club) if plan and club else None,
        member_name=(f"{member.first_name} {member.last_name}" if member else None),
        member_email=(member.email if member else None),
        assessment=(
            ClubAssessmentResponse.model_validate(assessment) if assessment else None
        ),
    )


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
        )
    )
    if operating_area_id:
        query = query.where(Club.operating_area_id == operating_area_id)
    if club_id:
        query = query.where(Club.id == club_id)
    rows = (
        await db.execute(query.order_by(Club.name, ClubPlanVersion.club_fee_kobo))
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
        await db.execute(
            query.order_by(Club.name, ClubPlanVersion.effective_from.desc())
        )
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
    plan = ClubPlanVersion(club_id=club_id, **body.model_dump())
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
    plan = await db.get(ClubPlanVersion, body.plan_version_id)
    today = date.today()
    if (
        plan is None
        or not plan.is_active
        or plan.effective_from > today
        or (plan.effective_to and plan.effective_to < today)
    ):
        raise HTTPException(status_code=400, detail="This Club plan is not available")
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
        **body.model_dump(),
    )
    db.add(application)
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
        plan = await db.get(ClubPlanVersion, application.plan_version_id)
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
            total = plan.club_fee_kobo + (
                plan.community_experience_fee_kobo
                if application.community_experience_selected
                else 0
            )
            lines.extend(
                [
                    "",
                    f"Your selected quarterly plan total before payment processing charges is NGN {total / 100:,.2f}.",
                    "Sign in to complete payment and onboarding.",
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
    if not (
        membership
        and membership.community_paid_until
        and membership.community_paid_until > utc_now()
    ):
        raise HTTPException(
            status_code=409,
            detail="Annual Community membership must be active before Club payment",
        )
    experience_fee = (
        plan.community_experience_fee_kobo
        if application.community_experience_selected
        else 0
    )
    return ClubPaymentContext(
        application_id=application.id,
        member_auth_id=member.auth_id,
        club_id=club.id,
        club_name=club.name,
        plan_version_id=plan.id,
        billing_cycle=plan.billing_cycle,
        currency=plan.currency,
        club_fee_kobo=plan.club_fee_kobo,
        community_experience_selected=application.community_experience_selected,
        community_experience_fee_kobo=experience_fee,
        subtotal_kobo=plan.club_fee_kobo + experience_fee,
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
    existing = (
        await db.execute(
            select(ClubEnrollment).where(
                ClubEnrollment.application_id == application.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        starts_at = body.starts_at or utc_now()
        db.add(
            ClubEnrollment(
                member_id=application.member_id,
                club_id=application.club_id,
                plan_version_id=application.plan_version_id,
                application_id=application.id,
                payment_reference=body.payment_reference,
                starts_at=starts_at,
                ends_at=starts_at + relativedelta(months=body.months),
                assigned_pod_id=application.preferred_pod_id,
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
