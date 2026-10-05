"""Admin CRUD + stats + by-auth lookup."""

"""Core members router - CRUD operations for member profiles."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.supabase import get_supabase_admin_client
from libs.db.session import get_async_db
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    Club,
    ClubEnrollment,
    CoachProfile,
    Member,
    MemberAvailability,
    MemberChallengeCompletion,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    Pod,
    PodAssignment,
    VolunteerInterest,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    resolve_member_media_urls,
)
from services.members_service.schemas import (
    MemberCreate,
    MemberListResponse,
    MemberMembershipResponse,
    MemberResponse,
    MemberUpdate,
)
from services.members_service.services.club_access import current_club_enrollment_until
from services.members_service.services.membership_status import (
    build_membership_status_summary,
)

logger = get_logger(__name__)
router = APIRouter()


async def _admin_member_response(member: Member, db: AsyncSession) -> dict:
    """Serialize an admin member with current dated Club access projected."""

    member_dict = MemberResponse.model_validate(member).model_dump()
    membership = member_dict.get("membership")
    if membership is not None:
        membership["club_enrollment_until"] = await current_club_enrollment_until(
            db,
            member_id=member.id,
            at=utc_now(),
        )
        member_dict["membership"] = MemberMembershipResponse.model_validate(
            membership
        ).model_dump()
    return await resolve_member_media_urls(member_dict)


@router.post("/", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_in: MemberCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Directly create a member (internal use or admin).
    Normal users should go through the pending registration flow.
    """
    query = select(Member).where(Member.email == member_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    member = Member(**member_in.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get("/", response_model=List[MemberListResponse])
async def list_members(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List all members (admin use)."""
    query = select(Member).options(*member_eager_load_options())
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Member.first_name.ilike(pattern),
                Member.last_name.ilike(pattern),
                Member.email.ilike(pattern),
                func.concat(Member.first_name, " ", Member.last_name).ilike(pattern),
            )
        )
    query = query.offset(skip).limit(limit).order_by(Member.created_at.desc())
    result = await db.execute(query)
    members = result.scalars().all()

    # Collect all media IDs for batch resolution
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    # Build the operational programme projection in batches. Club status comes
    # from dated location-specific enrollments; Pod placement comes from the
    # active assignment. This keeps the admin list off the legacy tier cache.
    member_ids = [member.id for member in members]
    now = utc_now()
    current_enrollments: dict[uuid.UUID, tuple[ClubEnrollment, Club]] = {}
    current_pods: dict[uuid.UUID, Pod] = {}
    if member_ids:
        enrollment_rows = (
            await db.execute(
                select(ClubEnrollment, Club)
                .join(Club, Club.id == ClubEnrollment.club_id)
                .where(
                    ClubEnrollment.member_id.in_(member_ids),
                    ClubEnrollment.status == "active",
                    ClubEnrollment.starts_at <= now,
                    ClubEnrollment.ends_at > now,
                )
                .order_by(ClubEnrollment.ends_at.desc())
            )
        ).all()
        for enrollment, club in enrollment_rows:
            current_enrollments.setdefault(enrollment.member_id, (enrollment, club))

        pod_rows = (
            await db.execute(
                select(PodAssignment, Pod)
                .join(Pod, Pod.id == PodAssignment.pod_id)
                .where(
                    PodAssignment.member_id.in_(member_ids),
                    PodAssignment.left_at.is_(None),
                )
            )
        ).all()
        for assignment, pod in pod_rows:
            current_pods[assignment.member_id] = pod

    responses: list[MemberListResponse] = []
    for member in members:
        base = MemberResponse.model_validate(member, from_attributes=True)
        payload = base.model_dump(
            exclude={
                "coach_profile",
                "profile",
                "membership",
                "emergency_contact",
                "availability",
                "preferences",
            }
        )
        payload["is_coach"] = bool(member.coach_profile)

        # Flatten profile fields
        if base.profile:
            p = base.profile
            payload["phone"] = p.phone
            payload["swim_level"] = p.swim_level
            payload["city"] = p.city
            payload["country"] = p.country
            payload["gender"] = p.gender
            payload["date_of_birth"] = p.date_of_birth
            payload["occupation"] = p.occupation
            payload["area_in_lagos"] = p.area_in_lagos
            payload["how_found_us"] = p.how_found_us
            payload["previous_communities"] = p.previous_communities
            payload["hopes_from_swimbuddz"] = p.hopes_from_swimbuddz
            payload["goals_narrative"] = p.personal_goals

        # Flatten membership fields for compatibility, then add the canonical
        # independent programme projection used by the admin UI.
        enrollment_pair = current_enrollments.get(member.id)
        current_enrollment = enrollment_pair[0] if enrollment_pair else None
        current_club = enrollment_pair[1] if enrollment_pair else None
        current_pod = current_pods.get(member.id)
        if base.membership:
            m = base.membership
            payload["primary_tier"] = m.primary_tier
            payload["active_tiers"] = m.active_tiers
            payload["requested_tiers"] = m.requested_tiers
            payload["community_paid_until"] = m.community_paid_until
            payload["club_paid_until"] = m.club_paid_until
            payload["academy_paid_until"] = m.academy_paid_until
            payload["post_academy_club_until"] = m.post_academy_club_until

            summary = build_membership_status_summary(
                primary_tier=m.primary_tier,
                active_tiers=m.active_tiers,
                declared_tiers=m.declared_tiers,
                requested_tiers=m.requested_tiers,
                community_paid_until=m.community_paid_until,
                club_paid_until=m.club_paid_until,
                academy_paid_until=m.academy_paid_until,
                post_academy_club_until=m.post_academy_club_until,
                club_enrollment_until=(
                    current_enrollment.ends_at if current_enrollment else None
                ),
                pending_payment_reference=m.pending_payment_reference,
                pending_tier_payments=m.pending_tier_payments,
                now=now,
            )
            tier_statuses = summary["tier_statuses"]
            annual_status = tier_statuses["community"]
            club_status = tier_statuses["club"]
            academy_status = tier_statuses["academy"]
            payload["annual_membership_status"] = annual_status["status"]
            payload["annual_membership_label"] = annual_status["label"]
            payload["annual_membership_paid_until"] = annual_status["effective_until"]
            payload["club_programme_status"] = club_status["status"]
            payload["club_programme_label"] = club_status["label"]
            payload["academy_programme_status"] = academy_status["status"]
            payload["academy_programme_label"] = academy_status["label"]
            payload["pending_programmes"] = [
                programme
                for programme in ("club", "academy")
                if tier_statuses[programme]["status"]
                in {"requested", "payment_pending", "approved_unpaid"}
            ]

        if current_enrollment and current_club:
            payload["current_club_id"] = current_club.id
            payload["current_club_name"] = current_club.name
            payload["current_club_payment_mode"] = current_enrollment.payment_mode
            payload["current_club_until"] = current_enrollment.ends_at
        if current_pod:
            payload["current_pod_id"] = current_pod.id
            payload["current_pod_name"] = current_pod.handle or current_pod.name

        # Flatten emergency contact
        if base.emergency_contact:
            ec = base.emergency_contact
            payload["emergency_contact_name"] = ec.name
            payload["emergency_contact_phone"] = ec.phone
            payload["medical_info"] = ec.medical_info

        # Add resolved photo URL
        if member.profile_photo_media_id:
            payload["profile_photo_url"] = url_map.get(member.profile_photo_media_id)

        responses.append(MemberListResponse(**payload))
    return responses


@router.get("/stats")
async def get_member_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """Get member statistics."""
    query = select(func.count(Member.id))
    result = await db.execute(query)
    total_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "approved")
    result = await db.execute(query)
    approved_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "pending")
    result = await db.execute(query)
    pending_approvals = result.scalar_one() or 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "approved_members": approved_members,
        "pending_approvals": pending_approvals,
    }


@router.get("/by-auth/{auth_id}", response_model=MemberResponse)
async def get_member_by_auth_id(
    auth_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by their Supabase auth_id.
    Used for service-to-service lookups (e.g., payments service).
    """
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a member by ID (admin use)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return await _admin_member_response(member, db)


@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: uuid.UUID,
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a member by ID (admin only)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    updated_member = result.scalar_one()

    return await _admin_member_response(updated_member, db)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a member by ID (admin only)."""
    query = select(Member).where(Member.id == member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Delete from Supabase Auth
    try:
        supabase = get_supabase_admin_client()
        if member.auth_id:
            supabase.auth.admin.delete_user(member.auth_id)
            logger.info(
                "Deleted Supabase user",
                extra={"extra_fields": {"auth_id": member.auth_id}},
            )
    except Exception as e:
        logger.error(
            "Failed to delete Supabase user",
            extra={"extra_fields": {"auth_id": member.auth_id, "error": str(e)}},
        )

    # Delete all related sub-tables
    await db.execute(delete(MemberProfile).where(MemberProfile.member_id == member.id))
    await db.execute(
        delete(MemberEmergencyContact).where(
            MemberEmergencyContact.member_id == member.id
        )
    )
    await db.execute(
        delete(MemberAvailability).where(MemberAvailability.member_id == member.id)
    )
    await db.execute(
        delete(MemberMembership).where(MemberMembership.member_id == member.id)
    )
    await db.execute(
        delete(MemberPreferences).where(MemberPreferences.member_id == member.id)
    )
    await db.execute(delete(CoachProfile).where(CoachProfile.member_id == member.id))
    await db.execute(
        delete(VolunteerInterest).where(VolunteerInterest.member_id == member.id)
    )
    await db.execute(
        delete(MemberChallengeCompletion).where(
            MemberChallengeCompletion.member_id == member.id
        )
    )

    await db.delete(member)
    await db.commit()
    return None
